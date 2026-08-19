#!/usr/bin/env python3
"""Clone a MiMo-generated narrator anchor with the local Qwen3-TTS 0.6B model."""

from __future__ import annotations

import argparse
import json
import time
import wave
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = PROJECT_ROOT / "models" / "Qwen3-TTS-12Hz-0.6B-Base"
DEFAULT_REFERENCE = PROJECT_ROOT / "previews" / "mimo_narrator_demo.wav"
DEFAULT_OUTPUT = PROJECT_ROOT / "previews" / "qwen_clone_from_mimo_1.wav"
DEFAULT_TEXT = (
    "暮色渐沉，远处风声掠过竹林。山路尽头，一盏微弱的灯火摇曳不定，"
    "而江湖中的故事，才刚刚开始。"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--ref-audio", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--ref-text", default=DEFAULT_TEXT)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    for label, path in (("model", args.model), ("reference audio", args.ref_audio)):
        if not path.exists():
            raise FileNotFoundError(f"Missing {label}: {path}")

    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available in this Python environment")

    torch.manual_seed(20260803)
    torch.cuda.manual_seed_all(20260803)
    load_started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        str(args.model.resolve()),
        device_map="cuda:0",
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
    )
    torch.cuda.synchronize()
    load_seconds = time.perf_counter() - load_started

    prompt_started = time.perf_counter()
    voice_prompt = model.create_voice_clone_prompt(
        ref_audio=str(args.ref_audio.resolve()),
        ref_text=args.ref_text,
        x_vector_only_mode=False,
    )
    torch.cuda.synchronize()
    prompt_seconds = time.perf_counter() - prompt_started

    generation_started = time.perf_counter()
    with torch.inference_mode():
        waves, sample_rate = model.generate_voice_clone(
            text=args.text,
            language="Chinese",
            voice_clone_prompt=voice_prompt,
        )
    torch.cuda.synchronize()
    generation_seconds = time.perf_counter() - generation_started

    if len(waves) != 1:
        raise RuntimeError(f"Expected one waveform, received {len(waves)}")
    audio = np.asarray(waves[0], dtype=np.float32)
    if not np.isfinite(audio).all() or len(audio) < sample_rate // 8:
        raise RuntimeError("Qwen returned invalid audio")

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.wav")
    sf.write(str(temporary), audio, sample_rate, subtype="PCM_16")
    temporary.replace(output)

    with wave.open(str(output), "rb") as handle:
        duration = handle.getnframes() / handle.getframerate()
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
    metadata = {
        "model": str(args.model.resolve()),
        "reference_audio": str(args.ref_audio.resolve()),
        "reference_text": args.ref_text,
        "text": args.text,
        "output": str(output),
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width_bytes": sample_width,
        "duration_seconds": round(duration, 3),
        "load_seconds": round(load_seconds, 3),
        "prompt_seconds": round(prompt_seconds, 3),
        "generation_seconds": round(generation_seconds, 3),
        "seed": 20260803,
        "x_vector_only_mode": False,
    }
    output.with_suffix(".json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
