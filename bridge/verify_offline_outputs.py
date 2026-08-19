#!/usr/bin/env python3
"""Verify that every offline TTS job and voice anchor has an output file."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JOBS_PATH = PROJECT_ROOT / "offline" / "manifest" / "dialogue_jobs.jsonl"
ANCHORS_PATH = PROJECT_ROOT / "offline" / "manifest" / "voice_anchors.json"


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def main() -> int:
    jobs = [
        json.loads(line)
        for line in JOBS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    path_counts = Counter(job["audio_file"] for job in jobs)
    missing_audio = [path for path in path_counts if not resolve_path(path).is_file()]
    empty_audio = [
        path
        for path in path_counts
        if resolve_path(path).is_file() and resolve_path(path).stat().st_size == 0
    ]
    actual_audio = set(
        str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
        for path in (PROJECT_ROOT / "offline" / "audio").rglob("*.wav")
    )
    expected_audio = set(path_counts)

    anchors = json.loads(ANCHORS_PATH.read_text(encoding="utf-8"))
    anchor_paths = {item["anchor_file"] for item in anchors}
    missing_anchors = [path for path in anchor_paths if not resolve_path(path).is_file()]
    empty_anchors = [
        path
        for path in anchor_paths
        if resolve_path(path).is_file() and resolve_path(path).stat().st_size == 0
    ]

    result = {
        "dialogue_jobs": len(jobs),
        "unique_dialogue_paths": len(expected_audio),
        "duplicate_path_jobs": len(jobs) - len(expected_audio),
        "actual_dialogue_wavs": len(actual_audio),
        "missing_dialogue_paths": len(missing_audio),
        "empty_dialogue_paths": len(empty_audio),
        "unreferenced_dialogue_wavs": len(actual_audio - expected_audio),
        "voice_groups": len(anchors),
        "unique_anchor_paths": len(anchor_paths),
        "missing_anchor_paths": len(missing_anchors),
        "empty_anchor_paths": len(empty_anchors),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if missing_audio:
        print("Missing dialogue outputs:")
        print("\n".join(missing_audio[:20]))
    if missing_anchors:
        print("Missing anchor outputs:")
        print("\n".join(missing_anchors[:20]))
    return 1 if missing_audio or empty_audio or missing_anchors or empty_anchors else 0


if __name__ == "__main__":
    raise SystemExit(main())
