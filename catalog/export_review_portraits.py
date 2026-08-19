#!/usr/bin/env python3
"""Export portraits referenced by the voice-profile review queue.

The game textures are already unpacked as ``.uasset``/``.uexp`` pairs.  This
script calls the project-local UE Viewer binary once per unique portrait and
keeps a resumable manifest, so an interrupted run only retries missing files.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = PROJECT_ROOT / "catalog"
DEFAULT_QUEUE = CATALOG_DIR / "profile_review_queue.json"
DEFAULT_SOURCE = PROJECT_ROOT / "extracted" / "portraits_source"
DEFAULT_UMODEL = PROJECT_ROOT / "tools" / "ueviewer" / "umodel.exe"
DEFAULT_OUTPUT = CATALOG_DIR / "portraits"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def package_path(asset_reference: str) -> str:
    """Return the Unreal package part of ``/Game/A/B.B``."""

    return asset_reference.rsplit(".", 1)[0]


def paths_for_asset(
    asset_reference: str, source_root: Path, output_root: Path
) -> tuple[Path, Path]:
    package = package_path(asset_reference)
    if not package.startswith("/Game/"):
        raise ValueError(f"Unsupported asset root: {asset_reference}")
    relative = Path(*package.removeprefix("/Game/").split("/"))
    source = source_root / "Wandering_Sword" / "Content" / relative.with_suffix(
        ".uasset"
    )
    output = output_root / relative.with_suffix(".png")
    return source, output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--umodel", type=Path, default=DEFAULT_UMODEL)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    queue = read_json(args.queue.resolve())
    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    umodel = args.umodel.resolve()
    if not umodel.is_file():
        raise FileNotFoundError(f"UE Viewer not found: {umodel}")

    consumers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for group in queue:
        for asset in group.get("portrait_assets") or []:
            consumers[asset].append(
                {
                    "voice_group_id": group["voice_group_id"],
                    "name": group.get("name", ""),
                    "gender": group.get("gender", "unknown"),
                    "age_group": group.get("age_group", "unknown"),
                    "review_reasons": group.get("review_reasons", []),
                    "speakable_line_count": group.get("speakable_line_count", 0),
                }
            )

    output_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    exported = skipped = missing = failed = 0
    total = len(consumers)

    for index, asset in enumerate(sorted(consumers), start=1):
        source, expected_output = paths_for_asset(asset, source_root, output_root)
        record: dict[str, Any] = {
            "asset_reference": asset,
            "source_uasset": str(source),
            "png": str(expected_output),
            "consumers": consumers[asset],
        }
        if not source.is_file() or not source.with_suffix(".uexp").is_file():
            record["status"] = "missing_source"
            missing += 1
        elif expected_output.is_file() and not args.force:
            record["status"] = "already_exported"
            skipped += 1
        else:
            command = [
                str(umodel),
                "-export",
                "-game=ue4.25+",
                "-png",
                f"-out={output_root}",
                str(source),
            ]
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if result.returncode == 0 and expected_output.is_file():
                record["status"] = "exported"
                exported += 1
            else:
                record["status"] = "failed"
                record["returncode"] = result.returncode
                record["log_tail"] = (result.stdout + "\n" + result.stderr)[-2000:]
                failed += 1
        records.append(record)
        if index == total or index % 20 == 0:
            print(
                f"[{index}/{total}] exported={exported} skipped={skipped} "
                f"missing={missing} failed={failed}",
                flush=True,
            )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "queue": str(args.queue.resolve()),
        "source_root": str(source_root),
        "output_root": str(output_root),
        "umodel": str(umodel),
        "engine_override": "ue4.25+ (UE4 4.25-4.27 compatibility mode)",
        "unique_portraits": total,
        "exported": exported,
        "already_exported": skipped,
        "missing_source": missing,
        "failed": failed,
        "portraits": records,
    }
    write_json(output_root / "manifest.json", manifest)
    print(json.dumps({k: manifest[k] for k in (
        "unique_portraits", "exported", "already_exported", "missing_source", "failed"
    )}, ensure_ascii=False, indent=2))
    return 1 if failed or missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
