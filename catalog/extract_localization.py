#!/usr/bin/env python3
"""Extract all shipped localization resources required by the voice catalog."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPAK = PROJECT_ROOT / "tools" / "repak" / "repak.exe"
DEFAULT_OUTPUT = PROJECT_ROOT / "extracted" / "catalog_source"
PAK_RELATIVE = Path("Wandering_Sword/Content/Paks/Wandering_Sword-WindowsNoEditor.pak")
INCLUDE_PATH = "Wandering_Sword/Content/Localization"
REQUIRED_RELATIVE = (
    Path("Wandering_Sword/Content/Localization/Npc/zh-Hans/Npc.locres"),
    Path(
        "Wandering_Sword/Content/Localization/Quests任务表/zh-Hans/"
        "Quests任务表.locres"
    ),
    Path("Wandering_Sword/Content/Localization/CG表/zh-Hans/CG表.locres"),
    Path(
        "Wandering_Sword/Content/Localization/门派地图与提示/zh-Hans/"
        "门派地图与提示.locres"
    ),
    Path(
        "Wandering_Sword/Content/Localization/程序_导出/zh-Hans/"
        "程序_导出.locres"
    ),
)


def resolve_game_root(requested: Path | None) -> Path:
    candidates: list[Path] = []
    if requested:
        candidates.append(requested.expanduser())
    environment_root = os.environ.get("WS_GAME_ROOT", "").strip()
    if environment_root:
        candidates.append(Path(environment_root))
    for drive in ("C", "D", "E", "F"):
        candidates.extend(
            (
                Path(f"{drive}:/Program/Steam/steamapps/common/Wandering Sword"),
                Path(f"{drive}:/Steam/steamapps/common/Wandering Sword"),
                Path(f"{drive}:/Program Files (x86)/Steam/steamapps/common/Wandering Sword"),
            )
        )
    for candidate in candidates:
        if (candidate / PAK_RELATIVE).is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        "Wandering Sword game root was not found. Pass --game-root or set "
        "WS_GAME_ROOT."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-root", type=Path)
    parser.add_argument("--repak", type=Path, default=DEFAULT_REPAK)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    game_root = resolve_game_root(args.game_root)
    pak = game_root / PAK_RELATIVE
    repak = args.repak.resolve()
    output = args.output_dir.resolve()
    if not repak.is_file():
        raise FileNotFoundError(f"repak is missing: {repak}")
    output.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            str(repak),
            "unpack",
            "-q",
            "-f",
            "-o",
            str(output),
            "-i",
            INCLUDE_PATH,
            str(pak),
        ],
        check=True,
    )

    missing = [str(path) for path in REQUIRED_RELATIVE if not (output / path).is_file()]
    if missing:
        raise RuntimeError(f"Required localization files were not extracted: {missing}")
    locres = sorted(output.rglob("*.locres"))
    result = {
        "game_root": str(game_root),
        "pak": str(pak),
        "output": str(output),
        "locres_files": len(locres),
        "bytes": sum(path.stat().st_size for path in locres),
        "required_files": len(REQUIRED_RELATIVE),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
