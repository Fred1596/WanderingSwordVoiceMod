#!/usr/bin/env python3
"""Compact UAssetGUI DataTable JSON into voice-catalog metadata.

UAssetGUI deliberately emits a lossless representation, which makes the two
source files several hundred megabytes.  The voice pipeline only needs identity,
description, gameplay role hints, and visual asset references.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = PROJECT_ROOT / "catalog" / "raw"


def property_map(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item.get("Name", ""): item
        for item in row.get("Value", [])
        if isinstance(item, dict) and item.get("Name")
    }


def primitive(properties: dict[str, dict[str, Any]], name: str) -> Any:
    item = properties.get(name)
    return item.get("Value") if item else None


def localized_text(properties: dict[str, dict[str, Any]], name: str) -> str:
    item = properties.get(name) or {}
    return (item.get("CultureInvariantString") or "").strip()


def localization_key(properties: dict[str, dict[str, Any]], name: str) -> str:
    item = properties.get(name) or {}
    value = item.get("Value")
    return value if isinstance(value, str) else ""


def soft_object_path(properties: dict[str, dict[str, Any]], name: str) -> str:
    item = properties.get(name) or {}
    value = item.get("Value")
    if not isinstance(value, dict):
        return ""
    asset_path = value.get("AssetPath")
    if not isinstance(asset_path, dict):
        return ""
    # UAssetAPI 1.1 serializes these UE4 assets into AssetName rather than
    # PackageName. Values contain both package and object, e.g. /Game/A/B.B.
    path = asset_path.get("PackageName") or asset_path.get("AssetName") or ""
    return "" if path == "None" else path


def simple_array(properties: dict[str, dict[str, Any]], name: str) -> list[Any]:
    value = primitive(properties, name)
    if not isinstance(value, list):
        return []
    result: list[Any] = []
    for item in value:
        if isinstance(item, (str, int, float, bool)) or item is None:
            result.append(item)
        elif isinstance(item, dict) and set(item).issuperset({"Value"}):
            nested = item.get("Value")
            if isinstance(nested, (str, int, float, bool)) or nested is None:
                result.append(nested)
    return result


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    exports = document.get("Exports") or []
    if len(exports) != 1:
        raise ValueError(f"Expected one export in {path}, got {len(exports)}")
    table = exports[0].get("Table") or {}
    rows = table.get("Data")
    if not isinstance(rows, list):
        raise ValueError(f"DataTable rows not found in {path}")
    return rows


def compact_npcs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        properties = property_map(row)
        npc_id = primitive(properties, "Id")
        output.append(
            {
                "row_key": row.get("Name", ""),
                "npc_id": npc_id,
                "name": localized_text(properties, "Name"),
                "name_localization_key": localization_key(properties, "Name"),
                "description": localized_text(properties, "Description"),
                "description_localization_key": localization_key(
                    properties, "Description"
                ),
                "resource_name": primitive(properties, "ResourceName") or "",
                "function_name": localized_text(properties, "FunctionName"),
                "function_type": primitive(properties, "FunctionType") or "",
                "guild_id": primitive(properties, "GuildId"),
                "level": primitive(properties, "Level"),
                "hobbies": simple_array(properties, "Hobbies"),
                "weapon_limits": simple_array(properties, "WeaponLimits"),
                "show_name": bool(primitive(properties, "bShowName")),
                "hidden_from_ui": bool(primitive(properties, "bNotShowInUI")),
                "can_fight": not bool(primitive(properties, "bCantFighting")),
            }
        )
    return output


def compact_resources(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    image_fields = (
        "HeadImage",
        "DlgImage",
        "TeamImage",
        "FightImage",
        "HeadImage_mobile",
        "DlgImage_mobile",
        "TeamImage_mobile",
        "FightImage_mobile",
        "HeadSprite",
        "IdleDownFlipbook",
    )
    output: list[dict[str, Any]] = []
    for row in rows:
        properties = property_map(row)
        output.append(
            {
                "resource_name": row.get("Name", ""),
                "assets": {
                    field: soft_object_path(properties, field) for field in image_fields
                },
                "one_face": bool(primitive(properties, "bOneFace")),
                "talk_face_directly": not bool(
                    primitive(properties, "bCantFaceToTalkDirect")
                ),
            }
        )
    return output


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    args = parser.parse_args()
    raw_dir = args.raw_dir.resolve()

    npc_source = raw_dir / "NPCs.json"
    resource_source = raw_dir / "NPCResources.json"
    npc_output = raw_dir / "NPCs.compact.json"
    resource_output = raw_dir / "NPCResources.compact.json"

    npcs = compact_npcs(load_rows(npc_source))
    write_json(npc_output, npcs)
    resources = compact_resources(load_rows(resource_source))
    write_json(resource_output, resources)

    stats = {
        "npc_rows": len(npcs),
        "npc_rows_with_name": sum(bool(row["name"]) for row in npcs),
        "npc_rows_with_description": sum(bool(row["description"]) for row in npcs),
        "npc_rows_with_resource": sum(bool(row["resource_name"]) for row in npcs),
        "distinct_resources_used": len(
            {row["resource_name"] for row in npcs if row["resource_name"]}
        ),
        "resource_rows": len(resources),
        "resource_rows_with_dialogue_image": sum(
            bool(row["assets"]["DlgImage"]) for row in resources
        ),
        "resource_rows_with_head_sprite": sum(
            bool(row["assets"]["HeadSprite"]) for row in resources
        ),
        "outputs": {
            "npcs": str(npc_output),
            "resources": str(resource_output),
        },
    }
    write_json(raw_dir / "game_metadata_stats.json", stats)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
