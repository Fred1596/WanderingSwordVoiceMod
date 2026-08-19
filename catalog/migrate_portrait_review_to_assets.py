#!/usr/bin/env python3
"""Freeze resolved portrait demographics under stable Unreal asset keys.

The original review UI stored classifications by contact-sheet index.  Those
indices change whenever a new review queue is rendered.  This one-time
migration reads the last successfully resolved profiles and records only the
fields whose evidence came from the game-portrait review.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = PROJECT_ROOT / "catalog"
OUTPUT = CATALOG_DIR / "portraits" / "portrait_demographics_by_asset.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    profiles = read_json(
        CATALOG_DIR / "profiles" / "character_profiles_resolved.json"
    )
    registry = read_json(CATALOG_DIR / "character_registry.json")
    registry_by_id = {str(item["character_id"]): item for item in registry}
    by_asset: dict[str, dict[str, Any]] = {}
    consumers: dict[str, list[str]] = defaultdict(list)

    for profile in profiles:
        portrait_fields = {
            item.get("field")
            for item in profile.get("resolution_evidence", [])
            if item.get("source") == "game_portrait_visual_review"
        }
        if not portrait_fields:
            continue
        character_id = str(profile["character_id"])
        character = registry_by_id[character_id]
        asset = (character.get("asset_metadata") or {}).get("portrait_asset")
        if not asset:
            continue
        values = by_asset.setdefault(asset, {})
        for field in ("gender", "age_group"):
            if field not in portrait_fields:
                continue
            value = profile.get(field)
            previous = values.get(field)
            if previous and previous != value:
                raise ValueError(
                    f"Conflicting {field} review for {asset}: {previous} vs {value}"
                )
            values[field] = value
        consumers[asset].append(character_id)

    document = {
        "format": 1,
        "review_method": "game_portrait_visual_review",
        "note": "Stable asset-keyed migration from the original reviewed contact sheets.",
        "by_asset": {
            asset: {
                **values,
                "source_character_ids": sorted(consumers[asset]),
            }
            for asset, values in sorted(by_asset.items())
        },
    }
    OUTPUT.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(by_asset)} stable portrait classifications to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
