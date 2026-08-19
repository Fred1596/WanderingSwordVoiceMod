#!/usr/bin/env python3
"""Generate all per-group synthetic voice anchors, resumably and in batches."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "voice_profiles.json"
DEFAULT_PLAN = PROJECT_ROOT / "offline" / "manifest" / "voice_anchors.json"
DEFAULT_MANIFEST = PROJECT_ROOT / "offline" / "anchors" / "manifest.json"


def format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--batch-size", type=int, default=2, choices=(1, 2, 3, 4, 6, 8, 12, 16)
    )
    parser.add_argument("--limit", type=int, help="Only generate this many pending anchors")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    config = json.loads(args.config.read_text(encoding="utf-8"))
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    model_cfg = config["design_model"]
    model_path = resolve_path(model_cfg["path"])
    if not model_path.is_dir():
        raise FileNotFoundError(f"VoiceDesign model not found: {model_path}")

    manifest_path = args.manifest.resolve()
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"model": model_path.name, "voices": {}, "batches": []}
    manifest.setdefault("voices", {})
    manifest.setdefault("batches", [])

    pending: list[dict[str, Any]] = []
    reused = existing = 0
    for item in plan:
        output = resolve_path(item["anchor_file"])
        if item.get("reuse_existing"):
            if not output.is_file():
                raise FileNotFoundError(f"Approved anchor is missing: {output}")
            reused += 1
            continue
        if output.is_file() and not args.force:
            existing += 1
            continue
        pending.append(item)
    if args.limit is not None:
        pending = pending[: args.limit]

    print(
        f"[计划] pending={len(pending)} existing={existing} reused={reused} "
        f"batch={args.batch_size}",
        flush=True,
    )
    if not pending:
        return 0

    torch.cuda.reset_peak_memory_stats()
    load_started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        str(model_path),
        device_map=model_cfg.get("device", "cuda:0"),
        dtype=getattr(torch, model_cfg.get("dtype", "bfloat16")),
        attn_implementation=model_cfg.get("attention", "sdpa"),
    )
    torch.cuda.synchronize()
    load_seconds = time.perf_counter() - load_started
    print(f"[模型] loaded in {load_seconds:.2f}s", flush=True)

    completed = 0
    generation_seconds = 0.0
    processing_started = time.perf_counter()
    for start in range(0, len(pending), args.batch_size):
        batch = pending[start : start + args.batch_size]
        batch_ids = [item["voice_group_id"] for item in batch]
        seed_material = "|".join(batch_ids)
        seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:8], 16)
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        started = time.perf_counter()
        with torch.inference_mode():
            wavs, sample_rate = model.generate_voice_design(
                text=[item["anchor_text"] for item in batch],
                language=[model_cfg.get("language", "Chinese")] * len(batch),
                instruct=[item["voice_design_prompt"] for item in batch],
            )
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
        generation_seconds += elapsed
        if len(wavs) != len(batch):
            raise RuntimeError(f"Expected {len(batch)} waves, got {len(wavs)}")

        for item, wav in zip(batch, wavs, strict=True):
            output = resolve_path(item["anchor_file"])
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_suffix(".tmp.wav")
            sf.write(str(temporary), wav, sample_rate, subtype="PCM_16")
            temporary.replace(output)
            manifest["voices"][item["voice_group_id"]] = {
                "name": item.get("name", ""),
                "file": str(output.relative_to(PROJECT_ROOT)),
                "anchor_text": item["anchor_text"],
                "voice_design_prompt": item["voice_design_prompt"],
                "seed": seed,
                "sample_rate": sample_rate,
                "duration_seconds": round(len(wav) / sample_rate, 3),
            }
            completed += 1
        manifest["batches"].append(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "voice_group_ids": batch_ids,
                "seed": seed,
                "seconds": round(elapsed, 3),
            }
        )
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_json_atomic(manifest_path, manifest)
        total_elapsed = time.perf_counter() - processing_started
        eta_seconds = (
            total_elapsed / completed * (len(pending) - completed)
            if completed
            else 0.0
        )
        print(
            f"[progress] anchors={completed}/{len(pending)} "
            f"batch_seconds={elapsed:.2f} "
            f"elapsed={format_duration(total_elapsed)} "
            f"eta={format_duration(eta_seconds)}",
            flush=True,
        )

    metrics = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model_path.name,
        "completed": completed,
        "batch_size": args.batch_size,
        "load_seconds": round(load_seconds, 3),
        "generation_seconds": round(generation_seconds, 3),
        "seconds_per_anchor": round(generation_seconds / completed, 3),
        "peak_allocated_gib": round(torch.cuda.max_memory_allocated() / 1024**3, 3),
        "peak_reserved_gib": round(torch.cuda.max_memory_reserved() / 1024**3, 3),
    }
    write_json_atomic(manifest_path.parent / "last_run_metrics.json", metrics)
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
