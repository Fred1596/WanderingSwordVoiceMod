#!/usr/bin/env python3
"""Instant runtime player for the fully pre-generated dialogue library."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import sys
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GAME_ROOT = PROJECT_ROOT.parent
DEFAULT_LOOKUP = PROJECT_ROOT / "offline" / "manifest" / "runtime_lookup.json"
DEFAULT_EVENTS = (
    GAME_ROOT
    / "Wandering_Sword"
    / "Binaries"
    / "Win64"
    / "ue4ss"
    / "Mods"
    / "WanderingSwordVoiceProbe"
    / "dialogue_events.jsonl"
)


@dataclass(frozen=True)
class DialogueEvent:
    speaker: str
    text: str


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def lookup_key(speaker: str, text: str) -> str:
    return hashlib.sha256(f"{normalize(speaker)}\0{normalize(text)}".encode("utf-8")).hexdigest()


def parse_event(line: str) -> DialogueEvent | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    if value.get("event") != "dialogue":
        return None
    speaker = normalize(str(value.get("speaker", "") or "旁白"))
    text = normalize(str(value.get("text", "")))
    return DialogueEvent(speaker, text) if text else None


def tail_events(
    path: Path,
    output: queue.Queue[DialogueEvent],
    replay_existing: bool,
    stop: threading.Event,
) -> None:
    while not path.exists() and not stop.wait(1.0):
        print(f"[等待] 对话事件文件尚未出现：{path}", flush=True)
    if stop.is_set():
        return
    with path.open("r", encoding="utf-8") as stream:
        if not replay_existing:
            stream.seek(0, os.SEEK_END)
        while not stop.is_set():
            position = stream.tell()
            line = stream.readline()
            if not line:
                try:
                    if path.stat().st_size < position:
                        stream.seek(0)
                    else:
                        stop.wait(0.08)
                except FileNotFoundError:
                    stop.wait(0.25)
                continue
            event = parse_event(line)
            if event:
                output.put(event)


def stop_audio() -> None:
    if sys.platform == "win32":
        import winsound

        winsound.PlaySound(None, 0)


def play_audio(path: Path) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Offline playback currently requires Windows")
    import winsound

    winsound.PlaySound(
        str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookup", type=Path, default=DEFAULT_LOOKUP)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--replay-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--once", action="store_true", help="Process one dialogue event and exit"
    )
    args = parser.parse_args()

    document: dict[str, Any] = json.loads(args.lookup.read_text(encoding="utf-8"))
    exact = document["exact"]
    prefixes = sorted(
        document.get("prefix", []), key=lambda item: len(item["text_prefix"]), reverse=True
    )
    events: queue.Queue[DialogueEvent] = queue.Queue()
    stop = threading.Event()
    thread = threading.Thread(
        target=tail_events,
        args=(args.events.resolve(), events, args.replay_existing, stop),
        daemon=True,
    )
    thread.start()
    print(
        f"[就绪] 离线索引 {len(exact)} 条；不加载 AI 模型，台词出现后立即播放。",
        flush=True,
    )
    try:
        while True:
            event = events.get()
            selected = exact.get(lookup_key(event.speaker, event.text))
            if selected is None:
                selected = next(
                    (
                        item
                        for item in prefixes
                        if item["speaker"] == event.speaker
                        and event.text.startswith(item["text_prefix"])
                    ),
                    None,
                )
            if selected is None:
                print(f"[未收录] {event.speaker}：{event.text}", flush=True)
                if args.once:
                    return 2
                continue
            audio = Path(selected["audio_file"])
            if not audio.is_absolute():
                audio = PROJECT_ROOT / audio
            if not audio.is_file():
                print(f"[待生成] {event.speaker}：{event.text}", flush=True)
                if args.once:
                    return 3
                continue
            print(f"[播放] {event.speaker}：{event.text}", flush=True)
            if not args.dry_run:
                stop_audio()
                play_audio(audio)
            if args.once:
                return 0
    except KeyboardInterrupt:
        stop.set()
        stop_audio()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
