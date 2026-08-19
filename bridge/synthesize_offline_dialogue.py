#!/usr/bin/env python3
"""Synthesize all dialogue WAVs from per-group anchors, resumably."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "voice_profiles.json"
DEFAULT_ANCHORS = PROJECT_ROOT / "offline" / "manifest" / "voice_anchors.json"
DEFAULT_JOBS = PROJECT_ROOT / "offline" / "manifest" / "dialogue_jobs.jsonl"
DEFAULT_PROGRESS = PROJECT_ROOT / "offline" / "audio" / "progress.json"


def format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def batch_size_for(max_text_length: int, configured: int) -> int:
    if max_text_length > 120:
        return 1
    if max_text_length > 85:
        return min(configured, 2)
    if max_text_length > 58:
        return min(configured, 3)
    if max_text_length > 42:
        return min(configured, 4)
    return configured


def max_new_tokens_for(items: list[dict[str, Any]]) -> int:
    """Bound codec generation by dialogue length instead of the model's 8192 default."""
    max_units = max(
        max(1, len("".join(item["tts_text"].split()))) for item in items
    )
    requested = 48 + max_units * 5
    rounded = int(math.ceil(requested / 8) * 8)
    return min(2048, max(96, rounded))


def runaway_file_limit(text: str, floor_seconds: float) -> float:
    """A conservative resume-time threshold for clearly runaway legacy files."""
    units = max(1, len("".join(text.split())))
    return max(floor_seconds, units * 1.2 + 15.0)


def hit_codec_limit(duration: float, max_new_tokens: int) -> bool:
    # Qwen3-TTS 12Hz produces about 12.5 codec frames per second. Outputs
    # landing at this boundary did not emit EOS and must never be persisted.
    return duration >= max(0.1, (max_new_tokens - 2) / 12.5)


VOCALIZATION_CHARS = frozenset("咳啊呃嗯唔哈呵哼嘶呀哦噗嗤欸诶唉").union({"哎"})


def last_resort_clip_seconds(text: str) -> float:
    """Keep a useful opening utterance if every EOS retry still fails."""
    spoken = [char for char in text if char.isalpha()]
    if spoken and all(char in VOCALIZATION_CHARS for char in spoken):
        return min(3.5, max(1.2, len(spoken) * 0.75 + 0.7))
    units = max(1, len("".join(text.split())))
    return min(30.0, max(2.5, units * 0.32 + 2.0))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--anchors", type=Path, default=DEFAULT_ANCHORS)
    parser.add_argument("--jobs", type=Path, default=DEFAULT_JOBS)
    parser.add_argument("--progress", type=Path, default=DEFAULT_PROGRESS)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=6,
        choices=(1, 2, 3, 4, 5, 6, 8, 12, 16, 20, 24),
    )
    parser.add_argument("--limit", type=int, help="Generate only this many missing lines")
    parser.add_argument("--voice-groups", nargs="*", help="Only these voice group ids")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--purge-runaway-seconds",
        type=float,
        default=60.0,
        help="Regenerate existing WAVs that are unmistakably runaway (default: 60)",
    )
    args = parser.parse_args()

    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    config = json.loads(args.config.read_text(encoding="utf-8"))
    anchor_plan = json.loads(args.anchors.read_text(encoding="utf-8"))
    anchor_by_group = {item["voice_group_id"]: item for item in anchor_plan}
    selected_groups = set(args.voice_groups or [])

    jobs_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    purged_runaway: list[dict[str, Any]] = []
    total_jobs = existing = 0
    for job in iter_jsonl(args.jobs):
        total_jobs += 1
        if selected_groups and job["voice_group_id"] not in selected_groups:
            continue
        output = resolve_path(job["audio_file"])
        if output.is_file() and not args.force:
            reason = ""
            try:
                duration = float(sf.info(str(output)).duration)
                limit = runaway_file_limit(
                    job["tts_text"], args.purge_runaway_seconds
                )
                if duration >= limit:
                    reason = f"duration={duration:.3f}s limit={limit:.3f}s"
            except Exception as exc:
                reason = f"unreadable={type(exc).__name__}: {exc}"
            if not reason:
                existing += 1
                continue
            purged_runaway.append(
                {
                    "job_id": job["job_id"],
                    "voice_group_id": job["voice_group_id"],
                    "audio_file": job["audio_file"],
                    "tts_text": job["tts_text"],
                    "reason": reason,
                }
            )
            output.unlink()
        jobs_by_group[job["voice_group_id"]].append(job)

    if purged_runaway:
        purge_log = PROJECT_ROOT / "logs" / (
            "purged_runaway_audio_"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + ".jsonl"
        )
        purge_log.parent.mkdir(parents=True, exist_ok=True)
        purge_log.write_text(
            "".join(
                json.dumps(item, ensure_ascii=False) + "\n"
                for item in purged_runaway
            ),
            encoding="utf-8",
        )
        print(
            f"[清理] runaway={len(purged_runaway)} log={purge_log}",
            flush=True,
        )

    # Keep plan priority between groups, but bucket similar text lengths inside
    # each voice to reduce padding and peak KV-cache memory.
    ordered_groups = [
        item["voice_group_id"]
        for item in anchor_plan
        if item["voice_group_id"] in jobs_by_group
    ]
    pending_count = sum(len(items) for items in jobs_by_group.values())
    if args.limit is not None and pending_count > args.limit:
        remaining = args.limit
        limited: dict[str, list[dict[str, Any]]] = {}
        for group_id in ordered_groups:
            chosen = jobs_by_group[group_id][:remaining]
            if chosen:
                limited[group_id] = chosen
                remaining -= len(chosen)
            if remaining <= 0:
                break
        jobs_by_group = defaultdict(list, limited)
        ordered_groups = [item for item in ordered_groups if item in limited]
        pending_count = args.limit

    print(
        f"[计划] total={total_jobs} existing={existing} pending={pending_count} "
        f"groups={len(ordered_groups)} max_batch={args.batch_size}",
        flush=True,
    )
    if not pending_count:
        return 0

    for group_id in ordered_groups:
        anchor_path = resolve_path(anchor_by_group[group_id]["anchor_file"])
        if not anchor_path.is_file():
            raise FileNotFoundError(
                f"Anchor not generated for {group_id}: {anchor_path}"
            )

    model_cfg = config["model"]
    model_path = resolve_path(model_cfg["path"])
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

    progress_path = args.progress.resolve()
    progress = (
        json.loads(progress_path.read_text(encoding="utf-8"))
        if progress_path.is_file()
        else {"runs": [], "completed_by_group": {}}
    )
    completed = failed = 0
    generated_audio_seconds = generation_seconds = 0.0
    batch_counts: Counter[int] = Counter()
    fallback_counts: Counter[str] = Counter()
    processing_started = time.perf_counter()

    def generate_batch(
        batch: list[dict[str, Any]],
        state: dict[str, Any],
        depth: int = 0,
        retry_index: int = 0,
    ) -> None:
        nonlocal completed, failed, generated_audio_seconds, generation_seconds
        if not batch:
            return
        mode = state["mode"]
        seed_material = mode + "|" + "|".join(item["job_id"] for item in batch)
        if retry_index:
            seed_material += f"|safe-retry-{retry_index}"
        seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:8], 16)
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        token_limit = max_new_tokens_for(batch)
        retry_kwargs: dict[str, Any] = {}
        if retry_index:
            retry_kwargs = {
                "do_sample": True,
                "top_k": 30,
                "top_p": 0.85,
                "temperature": 0.75,
                "repetition_penalty": 1.2,
                "subtalker_dosample": True,
                "subtalker_top_k": 30,
                "subtalker_top_p": 0.85,
                "subtalker_temperature": 0.75,
            }
        started = time.perf_counter()
        oom_error: torch.cuda.OutOfMemoryError | None = None
        try:
            with torch.inference_mode():
                wavs, sample_rate = model.generate_voice_clone(
                    text=[item["tts_text"] for item in batch],
                    language=[model_cfg.get("language", "Chinese")] * len(batch),
                    voice_clone_prompt=state[mode + "_prompt"],
                    non_streaming_mode=True,
                    max_new_tokens=token_limit,
                    **retry_kwargs,
                )
            torch.cuda.synchronize()
        except torch.cuda.OutOfMemoryError as exc:
            # Leave the exception block before retrying. Otherwise its traceback
            # can retain partial CUDA tensors and every recursive split sees less
            # free memory than the previous attempt.
            oom_error = exc
        if oom_error is not None:
            gc.collect()
            torch.cuda.empty_cache()
            if len(batch) == 1:
                failed += 1
                raise RuntimeError(
                    f"CUDA OOM while generating {batch[0]['job_id']}"
                ) from oom_error
            midpoint = len(batch) // 2
            print(
                f"[显存] batch={len(batch)} 自动拆分为 {midpoint}+{len(batch)-midpoint}",
                flush=True,
            )
            generate_batch(batch[:midpoint], state, depth + 1, retry_index)
            generate_batch(batch[midpoint:], state, depth + 1, retry_index)
            return
        elapsed = time.perf_counter() - started
        generation_seconds += elapsed
        batch_counts[len(batch)] += 1
        if len(wavs) != len(batch):
            raise RuntimeError(f"Expected {len(batch)} waves, got {len(wavs)}")

        arrays = [np.asarray(wav) for wav in wavs]
        durations = [len(array) / sample_rate for array in arrays]
        limit_hits = [
            index
            for index, duration in enumerate(durations)
            if hit_codec_limit(duration, token_limit)
        ]
        if limit_hits:
            fallback_counts[f"{mode}_limit_hits"] += len(limit_hits)
            print(
                f"[防失控] mode={mode} batch={len(batch)} hits={len(limit_hits)} "
                f"max_tokens={token_limit} max_audio={max(durations):.2f}s",
                flush=True,
            )
            if mode == "xvector" and len(batch) == 1 and retry_index >= 3:
                clip_seconds = last_resort_clip_seconds(batch[0]["tts_text"])
                clip_frames = min(len(arrays[0]), max(1, int(clip_seconds * sample_rate)))
                clipped = arrays[0][:clip_frames].copy()
                fade_frames = min(int(sample_rate * 0.08), len(clipped) // 4)
                if fade_frames:
                    clipped[-fade_frames:] *= np.linspace(
                        1.0, 0.0, fade_frames, dtype=clipped.dtype
                    )
                arrays = [clipped]
                durations = [len(clipped) / sample_rate]
                fallback_counts["last_resort_clips"] += 1
                print(
                    f"[最终裁短] job={batch[0]['job_id']} "
                    f"text={batch[0]['tts_text']!r} duration={durations[0]:.2f}s",
                    flush=True,
                )
                del wavs
                limit_hits = []
            else:
                del wavs, arrays
                gc.collect()
                torch.cuda.empty_cache()
            if limit_hits and mode == "icl":
                if state.get("xvector_prompt") is None:
                    state["xvector_prompt"] = model.create_voice_clone_prompt(
                        ref_audio=state["anchor_path"],
                        ref_text=None,
                        x_vector_only_mode=True,
                    )
                    torch.cuda.synchronize()
                state["mode"] = "xvector"
                fallback_counts["groups_switched_to_xvector"] += 1
                print(
                    f"[声纹回退] name={state['name']} ICL触顶，"
                    "本声线后续改用同锚点x-vector模式",
                    flush=True,
                )
                generate_batch(batch, state, depth + 1)
                return
            if limit_hits and len(batch) > 1:
                midpoint = len(batch) // 2
                generate_batch(batch[:midpoint], state, depth + 1)
                generate_batch(batch[midpoint:], state, depth + 1)
                return
            if limit_hits and retry_index < 3:
                fallback_counts["single_safe_retries"] += 1
                print(
                    f"[安全重试] job={batch[0]['job_id']} attempt={retry_index + 1}/3",
                    flush=True,
                )
                generate_batch(batch, state, depth + 1, retry_index + 1)
                return

        for job, array in zip(batch, arrays, strict=True):
            if not np.isfinite(array).all() or len(array) < sample_rate // 8:
                failed += 1
                raise RuntimeError(f"Invalid generated audio for {job['job_id']}")
            output = resolve_path(job["audio_file"])
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_suffix(".tmp.wav")
            sf.write(str(temporary), array, sample_rate, subtype="PCM_16")
            temporary.replace(output)
            completed += 1
            generated_audio_seconds += len(array) / sample_rate

        total_elapsed = time.perf_counter() - processing_started
        eta_seconds = (
            total_elapsed / completed * (pending_count - completed)
            if completed
            else 0.0
        )
        print(
            f"[progress] lines={completed}/{pending_count} batch={len(batch)} "
            f"batch_seconds={elapsed:.2f} "
            f"elapsed={format_duration(total_elapsed)} "
            f"eta={format_duration(eta_seconds)}",
            flush=True,
        )

    for group_index, group_id in enumerate(ordered_groups, start=1):
        anchor = anchor_by_group[group_id]
        group_jobs = jobs_by_group[group_id]
        group_jobs.sort(key=lambda item: (len(item["tts_text"]), item["job_id"]))
        prompt_started = time.perf_counter()
        anchor_path = str(resolve_path(anchor["anchor_file"]))
        prompt = model.create_voice_clone_prompt(
            ref_audio=anchor_path,
            ref_text=anchor["anchor_text"],
            x_vector_only_mode=False,
        )
        state = {
            "mode": "icl",
            "icl_prompt": prompt,
            "xvector_prompt": None,
            "anchor_path": anchor_path,
            "name": anchor.get("name", ""),
        }
        torch.cuda.synchronize()
        prompt_seconds = time.perf_counter() - prompt_started

        position = 0
        group_completed_before = completed
        while position < len(group_jobs):
            candidate_end = min(position + args.batch_size, len(group_jobs))
            candidate = group_jobs[position:candidate_end]
            effective = batch_size_for(
                max(len(item["tts_text"]) for item in candidate), args.batch_size
            )
            batch = group_jobs[position : position + effective]
            generate_batch(batch, state)
            position += len(batch)

        group_completed = completed - group_completed_before
        progress.setdefault("completed_by_group", {})[group_id] = (
            progress.get("completed_by_group", {}).get(group_id, 0) + group_completed
        )
        progress["updated_at"] = datetime.now(timezone.utc).isoformat()
        progress["last_group"] = group_id
        progress["last_group_name"] = anchor.get("name", "")
        progress["completed_in_current_run"] = completed
        progress["pending_at_run_start"] = pending_count
        write_json_atomic(progress_path, progress)
        print(
            f"[进度] group={group_index}/{len(ordered_groups)} "
            f"name={anchor.get('name','') or '(无名)'} lines={group_completed} "
            f"total={completed}/{pending_count} prompt={prompt_seconds:.2f}s "
            f"mode={state['mode']}",
            flush=True,
        )
        del prompt, state
        torch.cuda.empty_cache()

    metrics = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model_path.name,
        "completed": completed,
        "failed": failed,
        "load_seconds": round(load_seconds, 3),
        "generation_seconds": round(generation_seconds, 3),
        "seconds_per_line": round(generation_seconds / max(completed, 1), 3),
        "generated_audio_seconds": round(generated_audio_seconds, 3),
        "realtime_factor": round(
            generation_seconds / max(generated_audio_seconds, 0.001), 3
        ),
        "batch_counts": dict(batch_counts),
        "fallback_counts": dict(fallback_counts),
        "peak_allocated_gib": round(torch.cuda.max_memory_allocated() / 1024**3, 3),
        "peak_reserved_gib": round(torch.cuda.max_memory_reserved() / 1024**3, 3),
    }
    progress.setdefault("runs", []).append(metrics)
    write_json_atomic(progress_path, progress)
    write_json_atomic(progress_path.parent / "last_run_metrics.json", metrics)
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
