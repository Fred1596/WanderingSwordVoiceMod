#!/usr/bin/env python3
"""Generate one narrator preview with MiMo VoiceDesign without exposing API keys."""

from __future__ import annotations

import argparse
import base64
import json
import os
import wave
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "previews" / "mimo_narrator_demo.wav"
MODEL = "mimo-v2.5-tts-voicedesign"
BASE_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_PROMPT = (
    "一位四十五岁左右的男性武侠故事旁白，普通话标准，声音低沉醇厚、清晰有磁性，"
    "带少量饱经江湖的沧桑感。语速中等偏慢，节奏沉稳克制，像评书先生讲述江湖往事，"
    "但不过度戏剧化。整体冷静、有分量，不要播音腔，不要夸张表演。"
)
DEFAULT_TEXT = (
    "暮色渐沉，远处风声掠过竹林。山路尽头，一盏微弱的灯火摇曳不定，"
    "而江湖中的故事，才刚刚开始。"
)


def read_user_environment(name: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
        return str(value).strip() or None
    except (FileNotFoundError, OSError):
        return None


def find_api_key() -> tuple[str, str]:
    for name in ("MIMO_API_KEY", "mimo1", "mimo2", "mimo3", "mimo4", "mimo5"):
        value = (os.environ.get(name) or "").strip() or read_user_environment(name)
        if value:
            return name, value
    raise RuntimeError(
        "No MiMo key found in MIMO_API_KEY or mimo1..mimo5 environment variables"
    )


def audio_data_from_message(message: Any) -> str:
    audio = getattr(message, "audio", None)
    if audio is None:
        raise RuntimeError("MiMo response did not contain message.audio")
    if isinstance(audio, dict):
        data = audio.get("data")
    else:
        data = getattr(audio, "data", None)
    if not data:
        raise RuntimeError("MiMo response audio did not contain base64 data")
    return str(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    args = parser.parse_args()

    from openai import OpenAI

    key_name, api_key = find_api_key()
    print(f"Using configured key variable: {key_name}")
    print(f"Model: {MODEL}")
    client = OpenAI(api_key=api_key, base_url=BASE_URL, timeout=180.0)
    completion = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "user", "content": args.prompt},
            {"role": "assistant", "content": args.text},
        ],
        audio={"format": "wav", "optimize_text_preview": False},
    )
    audio_bytes = base64.b64decode(
        audio_data_from_message(completion.choices[0].message), validate=True
    )
    if not (audio_bytes.startswith(b"RIFF") and audio_bytes[8:12] == b"WAVE"):
        raise RuntimeError("MiMo returned data is not a RIFF/WAVE file")

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.wav")
    temporary.write_bytes(audio_bytes)
    temporary.replace(output)

    with wave.open(str(output), "rb") as handle:
        sample_rate = handle.getframerate()
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        frames = handle.getnframes()
    metadata = {
        "model": MODEL,
        "voice_design_prompt": args.prompt,
        "text": args.text,
        "output": str(output),
        "bytes": len(audio_bytes),
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width_bytes": sample_width,
        "duration_seconds": round(frames / sample_rate, 3),
        "optimize_text_preview": False,
    }
    output.with_suffix(".json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
