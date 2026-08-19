#!/usr/bin/env python3
"""Validate offline manifests and report reusable/pending generation work."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANCHOR_PLAN = PROJECT_ROOT / "offline" / "manifest" / "voice_anchors.json"
JOBS_PATH = PROJECT_ROOT / "offline" / "manifest" / "dialogue_jobs.jsonl"


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc


def resolve(relative_path: str) -> Path:
    path = Path(relative_path)
    return path if path.is_absolute() else PROJECT_ROOT / path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()

    anchors = json.loads(ANCHOR_PLAN.read_text(encoding="utf-8"))
    jobs = list(iter_jsonl(JOBS_PATH))
    group_ids = {item["voice_group_id"] for item in anchors}
    if len(group_ids) != len(anchors):
        raise ValueError("Duplicate voice_group_id in voice anchor plan")
    job_ids = {item["job_id"] for item in jobs}
    if len(job_ids) != len(jobs):
        raise ValueError("Duplicate job_id in dialogue job manifest")
    unknown_groups = sorted(
        {item["voice_group_id"] for item in jobs} - group_ids
    )
    if unknown_groups:
        raise ValueError(f"Dialogue jobs reference unknown voice groups: {unknown_groups[:5]}")

    ready_anchors = sum(resolve(item["anchor_file"]).is_file() for item in anchors)
    ready_audio = sum(resolve(item["audio_file"]).is_file() for item in jobs)
    nonverbal_jobs = sum(
        item.get("tts_text_strategy") == "nonverbal_vocalization" for item in jobs
    )
    invalid_tts = [item["job_id"] for item in jobs if not item.get("tts_text", "").strip()]
    if invalid_tts:
        raise ValueError(f"Jobs with empty TTS text: {invalid_tts[:5]}")

    report = {
        "status": "complete"
        if ready_anchors == len(anchors) and ready_audio == len(jobs)
        else "generation_pending",
        "voice_anchors": {
            "total": len(anchors),
            "reusable": ready_anchors,
            "pending": len(anchors) - ready_anchors,
        },
        "dialogue_audio": {
            "total": len(jobs),
            "reusable": ready_audio,
            "pending": len(jobs) - ready_audio,
        },
        "nonverbal_vocalization_jobs": nonverbal_jobs,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.require_complete and report["status"] != "complete":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
