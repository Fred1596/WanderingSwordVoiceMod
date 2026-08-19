#!/usr/bin/env python3
"""Benchmark resident-model single and batched Qwen voice cloning."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "voice_profiles.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "cache" / "offline_benchmark"

TEST_LINES = [
    "嗯，我明白了。",
    "少侠且慢，此事尚有几处疑点，待我细细说来。",
    "江湖之事看似纷乱，其实只要循着蛛丝马迹逐一查证，总能找到藏在背后的真相。",
    "前路纵然凶险，我既已答应诸位，便不会在此时退缩。",
    "客官来得正巧，店里刚备好了热茶，坐下歇一歇再赶路吧。",
    "此地山势复杂，入夜后更容易迷失方向，诸位务必跟紧一些。",
    "当年的事情已经过去很久，可每当想起，心中仍旧难以平静。",
    "既然线索都指向同一个地方，我们明日一早便动身前去查探。",
]


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--profile", default="yuwen_yi")
    parser.add_argument(
        "--batch-size", type=int, default=4, choices=(1, 2, 3, 4, 6, 8)
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    config: dict[str, Any] = json.loads(args.config.read_text(encoding="utf-8"))
    model_cfg = config["model"]
    profile = config["profiles"][args.profile]
    model_path = resolve_path(model_cfg["path"])
    anchor_path = resolve_path(profile["anchor_file"])
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        str(model_path),
        device_map=model_cfg.get("device", "cuda:0"),
        dtype=getattr(torch, model_cfg.get("dtype", "bfloat16")),
        attn_implementation=model_cfg.get("attention", "sdpa"),
    )
    torch.cuda.synchronize()
    load_seconds = time.perf_counter() - started

    started = time.perf_counter()
    prompt = model.create_voice_clone_prompt(
        ref_audio=str(anchor_path),
        ref_text=profile["anchor_text"],
        x_vector_only_mode=False,
    )
    torch.cuda.synchronize()
    prompt_seconds = time.perf_counter() - started

    texts = TEST_LINES[: args.batch_size]
    started = time.perf_counter()
    wavs, sample_rate = model.generate_voice_clone(
        text=texts,
        language=[model_cfg.get("language", "Chinese")] * len(texts),
        voice_clone_prompt=prompt,
    )
    torch.cuda.synchronize()
    generation_seconds = time.perf_counter() - started

    audio_seconds = 0.0
    for index, wav in enumerate(wavs, start=1):
        audio_seconds += len(wav) / sample_rate
        sf.write(
            str(output_dir / f"benchmark_{index:02d}.wav"),
            wav,
            sample_rate,
            subtype="PCM_16",
        )

    metrics = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model_path.name,
        "profile": args.profile,
        "batch_size": len(texts),
        "characters": sum(len(item) for item in texts),
        "load_seconds": round(load_seconds, 3),
        "prompt_seconds": round(prompt_seconds, 3),
        "generation_seconds": round(generation_seconds, 3),
        "seconds_per_line": round(generation_seconds / len(texts), 3),
        "audio_seconds": round(audio_seconds, 3),
        "realtime_factor": round(generation_seconds / audio_seconds, 3),
        "peak_allocated_gib": round(
            torch.cuda.max_memory_allocated() / (1024**3), 3
        ),
        "peak_reserved_gib": round(
            torch.cuda.max_memory_reserved() / (1024**3), 3
        ),
        "sample_rate": sample_rate,
        "output_dir": str(output_dir),
    }
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
