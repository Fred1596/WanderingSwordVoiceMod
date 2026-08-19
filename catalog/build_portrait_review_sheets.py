#!/usr/bin/env python3
"""Build labelled contact sheets for local portrait demographic review."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = PROJECT_ROOT / "catalog"
DEFAULT_MANIFEST = CATALOG_DIR / "portraits" / "manifest.json"
DEFAULT_OUTPUT = CATALOG_DIR / "portraits" / "review_sheets"

AGE_ZH = {
    "child": "儿童",
    "teen": "少年",
    "young_adult": "青年",
    "middle_aged": "中年",
    "elderly": "老年",
    "ageless": "无龄",
    "unknown": "?",
}
GENDER_ZH = {
    "male": "男",
    "female": "女",
    "nonhuman": "非人",
    "unknown": "?",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
    )
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def composite_portrait(path: Path, width: int, height: int) -> Image.Image:
    source = Image.open(path).convert("RGBA")
    canvas = Image.new("RGB", (width, height), "#d7d7d7")
    contained = ImageOps.contain(source, (width, height), Image.Resampling.LANCZOS)
    if contained.mode == "RGBA":
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        layer.alpha_composite(
            contained, ((width - contained.width) // 2, (height - contained.height) // 2)
        )
        canvas.paste(layer, mask=layer.getchannel("A"))
    else:
        canvas.paste(
            contained, ((width - contained.width) // 2, (height - contained.height) // 2)
        )
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--rows", type=int, default=4)
    args = parser.parse_args()

    manifest = read_json(args.manifest.resolve())
    records = [
        item
        for item in manifest["portraits"]
        if item["status"] in {"exported", "already_exported"}
        and Path(item["png"]).is_file()
    ]
    records.sort(
        key=lambda item: (
            -sum(int(x.get("speakable_line_count", 0)) for x in item["consumers"]),
            item["asset_reference"],
        )
    )
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    cell_width, image_height, text_height = 300, 245, 92
    cell_height = image_height + text_height
    per_sheet = args.columns * args.rows
    title_font = load_font(21)
    label_font = load_font(17)
    small_font = load_font(14)
    index_records: list[dict[str, Any]] = []

    for position, record in enumerate(records, start=1):
        consumers = sorted(
            record["consumers"],
            key=lambda item: -int(item.get("speakable_line_count", 0)),
        )
        names = list(dict.fromkeys(item.get("name", "") for item in consumers))
        record["review_index"] = position
        record["display_names"] = names
        index_records.append(record)

    sheet_count = math.ceil(len(index_records) / per_sheet)
    for sheet_number in range(sheet_count):
        subset = index_records[
            sheet_number * per_sheet : (sheet_number + 1) * per_sheet
        ]
        sheet = Image.new(
            "RGB",
            (args.columns * cell_width, args.rows * cell_height + 48),
            "#f3f3f3",
        )
        draw = ImageDraw.Draw(sheet)
        draw.text(
            (14, 10),
            f"逸剑风云诀 · 人物视觉复核 {sheet_number + 1}/{sheet_count}",
            fill="#202020",
            font=title_font,
        )
        for slot, record in enumerate(subset):
            row, column = divmod(slot, args.columns)
            x = column * cell_width
            y = 48 + row * cell_height
            portrait = composite_portrait(Path(record["png"]), cell_width, image_height)
            sheet.paste(portrait, (x, y))
            draw.rectangle(
                (x, y, x + cell_width - 1, y + cell_height - 1),
                outline="#777777",
                width=1,
            )
            main_consumer = record["consumers"][0]
            names = " / ".join(record["display_names"]) or "（无显示名）"
            if len(names) > 15:
                names = names[:14] + "…"
            current = (
                f"当前: {GENDER_ZH.get(main_consumer.get('gender'), '?')} / "
                f"{AGE_ZH.get(main_consumer.get('age_group'), '?')}"
            )
            line_count = sum(
                int(item.get("speakable_line_count", 0))
                for item in record["consumers"]
            )
            draw.text(
                (x + 8, y + image_height + 5),
                f"#{record['review_index']:03d}  {names}",
                fill="#111111",
                font=label_font,
            )
            draw.text(
                (x + 8, y + image_height + 35),
                current,
                fill="#333333",
                font=small_font,
            )
            draw.text(
                (x + 8, y + image_height + 58),
                f"台词 {line_count} · 组 {len(record['consumers'])}",
                fill="#555555",
                font=small_font,
            )

        output = output_dir / f"portrait_review_{sheet_number + 1:02d}.png"
        sheet.save(output, optimize=True)
        print(output)

    write_json(output_dir / "portrait_review_index.json", index_records)
    write_json(
        output_dir / "sheet_stats.json",
        {
            "portraits": len(index_records),
            "sheets": sheet_count,
            "columns": args.columns,
            "rows": args.rows,
            "index": str(output_dir / "portrait_review_index.json"),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
