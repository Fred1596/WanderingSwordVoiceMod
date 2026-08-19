#!/usr/bin/env python3
"""Record files missing from the local v1.0 generation for server packaging."""

from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_DIR = PROJECT_ROOT / "offline" / "manifest"


def main() -> int:
    anchors = json.loads(
        (MANIFEST_DIR / "voice_anchors.json").read_text(encoding="utf-8")
    )
    jobs = [
        json.loads(line)
        for line in (MANIFEST_DIR / "dialogue_jobs.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    pending = {
        item["anchor_file"]
        for item in anchors
        if not (PROJECT_ROOT / item["anchor_file"]).is_file()
    }
    pending.update(
        item["audio_file"]
        for item in jobs
        if not (PROJECT_ROOT / item["audio_file"]).is_file()
    )
    pending.update(
        {
            "offline/anchors/manifest.json",
            "offline/anchors/last_run_metrics.json",
            "offline/audio/progress.json",
            "offline/audio/last_run_metrics.json",
        }
    )
    output = MANIFEST_DIR / "server_delta_files.txt"
    output.write_text("\n".join(sorted(pending)) + "\n", encoding="utf-8")
    report = {
        "file_list": str(output),
        "pending_anchors": sum(
            not (PROJECT_ROOT / item["anchor_file"]).is_file() for item in anchors
        ),
        "pending_dialogue_audio": sum(
            not (PROJECT_ROOT / item["audio_file"]).is_file() for item in jobs
        ),
        "listed_paths_including_metrics": len(pending),
    }
    (MANIFEST_DIR / "server_delta_stats.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
