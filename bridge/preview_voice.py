#!/usr/bin/env python3
"""Synthesize one game line through the complete designed-voice clone pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from voice_runtime import (
    DEFAULT_CONFIG,
    PROJECT_ROOT,
    DialogueEvent,
    QwenVoiceCloneBackend,
    VoiceRouter,
)


def main(args: argparse.Namespace) -> int:
    with args.config.open("r", encoding="utf-8") as stream:
        config = json.load(stream)
    router = VoiceRouter(config)
    profile_name, profile = router.route(args.speaker)
    backend = QwenVoiceCloneBackend(config, PROJECT_ROOT / "cache" / "audio")
    output = backend.synthesize(
        DialogueEvent(speaker=args.speaker, text=args.text), profile_name, profile
    )
    print(f"[试听] {args.speaker} -> {profile_name}")
    print(f"[文件] {output}")
    if args.play:
        if sys.platform != "win32":
            raise RuntimeError("自动播放仅支持 Windows。")
        import winsound

        winsound.PlaySound(str(output), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="试听单个人物的完整 AI 配音链路")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--speaker", default="测试角色")
    parser.add_argument(
        "--text", default="前面的山路有些难走，我们先在这里稍作休整。"
    )
    parser.add_argument("--play", action="store_true")
    return parser


if __name__ == "__main__":
    try:
        raise SystemExit(main(build_parser().parse_args()))
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        raise SystemExit(1)
