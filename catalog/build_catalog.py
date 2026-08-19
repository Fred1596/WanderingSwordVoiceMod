#!/usr/bin/env python3
"""Build a speaker-aware dialogue catalog from Wandering Sword locres files.

The Simplified Chinese dialogue strings use this format:

    <speaker id> - <display name> $@$ <dialogue>

Keeping the numeric speaker id is important: display names are not guaranteed to
be unique and may change with titles or story state.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]

try:
    import pylocres
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "pylocres is required. Install the dependencies with "
        "`python -m pip install -r requirements-catalog.txt`."
    ) from exc


DEFAULT_LOCALIZATION_ROOT = (
    PROJECT_ROOT
    / "extracted"
    / "catalog_source"
    / "Wandering_Sword"
    / "Content"
    / "Localization"
)
DEFAULT_LOCRES_FILES = (
    ("npc", Path("Npc/zh-Hans/Npc.locres")),
    ("quest", Path("Quests任务表/zh-Hans/Quests任务表.locres")),
    ("cg", Path("CG表/zh-Hans/CG表.locres")),
    ("guide", Path("门派地图与提示/zh-Hans/门派地图与提示.locres")),
    ("program", Path("程序_导出/zh-Hans/程序_导出.locres")),
)

NARRATOR_CHARACTER_ID = "wsvoice_narrator"
NARRATOR_NAME = "旁白"
NARRATION_NAMESPACES = {("guide", "FullscreenScrollTexts")}
SYSTEM_NARRATION_KEYS = {
    ("program", "Game_Msg", "提示切换战斗模式"),
}

DIALOGUE_RE = re.compile(
    r"^\s*(?P<speaker_id>\d+)\s*-\s*(?P<speaker>.*?)\s*\$@\$\s*(?P<text>.*)\s*$",
    re.DOTALL,
)
NPC_METADATA_RE = re.compile(r"^(?P<npc_id>\d+)_(?P<field>Name|Description)$")
ROW_FIELD_RE = re.compile(r"^(?P<row_id>\d+)(?:_(?P<field>.*))?$")
RICH_TEXT_TAG_RE = re.compile(r"<[^>]*>")
SPEAKABLE_RE = re.compile(r"[\u3400-\u9fffA-Za-z0-9]")

FEMALE_TERMS = (
    "女性",
    "女子",
    "少女",
    "姑娘",
    "女侠",
    "夫人",
    "妇人",
    "老婆婆",
    "婆婆",
    "老妪",
    "小姐",
    "女弟子",
    "侍女",
    "丫鬟",
    "女童",
    "寡妇",
    "老板娘",
    "女掌柜",
)
MALE_TERMS = (
    "男性",
    "男子",
    "少年",
    "公子",
    "汉子",
    "壮汉",
    "大汉",
    "老汉",
    "老翁",
    "男弟子",
    "小厮",
    "书生",
)
FEMALE_IDENTITY_PATTERNS = (r"之女(?:$|[，,。；;])", r"的女儿(?:$|[，,。；;])")
MALE_IDENTITY_PATTERNS = (r"之子(?:$|[，,。；;])", r"的儿子(?:$|[，,。；;])")
AGE_TERMS = (
    (
        "elderly",
        (
            "老者",
            "老人",
            "老翁",
            "老汉",
            "老婆婆",
            "婆婆",
            "老妪",
            "年迈",
            "高龄",
            "长者",
            "耄耋",
        ),
    ),
    (
        "child",
        ("孩童", "小孩", "幼童", "男孩", "女孩", "女童", "童子", "年幼"),
    ),
    ("young_adult", ("少年", "少女", "青年", "年轻", "小伙", "姑娘", "公子")),
    ("middle_aged", ("中年", "大叔", "大婶", "壮年")),
)
ROLE_TERMS = (
    ("sect_leader", ("掌门",)),
    ("elder", ("长老",)),
    ("merchant", ("商人", "商贩", "掌柜", "店小二", "伙计")),
    ("official", ("官员", "捕快", "官差", "县令", "太守")),
    ("soldier", ("将军", "士兵", "守卫", "侍卫", "官兵")),
    ("physician", ("医师", "大夫", "郎中", "医者")),
    ("monk", ("僧人", "和尚", "住持")),
    ("daoist", ("道士", "道长")),
    ("disciple", ("弟子",)),
    ("assassin", ("杀手", "刺客")),
    ("bandit", ("山贼", "盗匪", "强盗")),
    ("fisher", ("渔夫", "船夫")),
    ("farmer", ("农民", "农夫")),
    ("scholar", ("书生", "学者")),
)
TEMPERAMENT_TERMS = (
    "沉稳",
    "和善",
    "豪爽",
    "活泼",
    "冷漠",
    "阴沉",
    "凶狠",
    "温柔",
    "正直",
    "狡猾",
    "谨慎",
    "冲动",
    "高傲",
    "木讷",
    "开朗",
    "腼腆",
    "端方",
)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = RICH_TEXT_TAG_RE.sub("", value)
    value = value.replace("#nl", "\n")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def compact_description(value: str) -> str:
    return re.sub(r"\s+", " ", normalize_text(value)).strip()


def iter_locres_entries(path: Path) -> Iterable[tuple[str, str, str]]:
    resource = pylocres.LocresFile()
    resource.read(path)
    for namespace in resource:
        for entry in namespace:
            yield namespace.name, entry.key, entry.translation


def source_info(domain: str, namespace: str, key: str) -> dict[str, str]:
    match = ROW_FIELD_RE.match(key)
    if match:
        return {
            "domain": domain,
            "namespace": namespace,
            "key": key,
            "row_id": match.group("row_id"),
            "field": match.group("field") or "",
        }
    return {
        "domain": domain,
        "namespace": namespace,
        "key": key,
        "row_id": "",
        "field": "",
    }


def numeric_sort_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_optional_game_metadata(
    output_dir: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    npc_path = output_dir / "raw" / "NPCs.compact.json"
    resource_path = output_dir / "raw" / "NPCResources.compact.json"
    if not npc_path.is_file() or not resource_path.is_file():
        return {}, {}
    npc_rows = json.loads(npc_path.read_text(encoding="utf-8"))
    resource_rows = json.loads(resource_path.read_text(encoding="utf-8"))
    npcs = {
        str(row["npc_id"]): row
        for row in npc_rows
        if row.get("npc_id") is not None
    }
    resources = {
        row["resource_name"]: row
        for row in resource_rows
        if row.get("resource_name")
    }
    return npcs, resources


def first_term(text_value: str, terms: Iterable[str]) -> str | None:
    return next((term for term in terms if term in text_value), None)


def infer_voice_traits(
    name: str,
    description: str,
    resource_name: str,
    assets: dict[str, str],
) -> dict[str, Any]:
    gender = None
    age_group = None
    role = None
    temperament: list[str] = []
    evidence: list[dict[str, str]] = []

    if name == "旁白":
        role = "narrator"
        evidence.append({"source": "game_display_name", "value": "旁白"})

    head_sprite = assets.get("HeadSprite", "")
    if "/Characters/DongWu/" in head_sprite:
        role = "nonhuman"
        gender = "nonhuman"
        evidence.append({"source": "head_sprite_asset", "value": head_sprite})

    if description:
        # Only the opening identity clause is deterministic. Relational words
        # later in a biography often describe somebody else ("与父亲居住",
        # "带着小师妹") and must not be assigned to the speaker.
        identity_clause = re.split(r"[，,。；;]", description, maxsplit=1)[0]
        female_term = first_term(identity_clause, FEMALE_TERMS)
        male_term = first_term(identity_clause, MALE_TERMS)
        if not female_term:
            female_term = next(
                (
                    match.group(0)
                    for pattern in FEMALE_IDENTITY_PATTERNS
                    if (match := re.search(pattern, identity_clause + "，"))
                ),
                None,
            )
        if not male_term:
            male_term = next(
                (
                    match.group(0)
                    for pattern in MALE_IDENTITY_PATTERNS
                    if (match := re.search(pattern, identity_clause + "，"))
                ),
                None,
            )
        if female_term and not male_term:
            gender = "female"
            evidence.append(
                {"source": "official_description", "value": female_term}
            )
        elif male_term and not female_term:
            gender = "male"
            evidence.append({"source": "official_description", "value": male_term})

        for candidate_age, terms in AGE_TERMS:
            matched = first_term(identity_clause, terms)
            if matched:
                age_group = candidate_age
                evidence.append(
                    {"source": "official_description", "value": matched}
                )
                break

        if role is None:
            for candidate_role, terms in ROLE_TERMS:
                matched = first_term(description, terms)
                if matched:
                    role = candidate_role
                    evidence.append(
                        {"source": "official_description", "value": matched}
                    )
                    break

        temperament = [term for term in TEMPERAMENT_TERMS if term in description]
        for term in temperament:
            evidence.append({"source": "official_description", "value": term})

    classified_fields = sum(
        value is not None for value in (gender, age_group, role)
    ) + bool(temperament)
    confidence = "high" if classified_fields >= 2 else "partial" if classified_fields else None
    return {
        "gender": gender,
        "age_group": age_group,
        "role": role,
        "temperament": temperament,
        "confidence": confidence,
        "evidence": evidence,
        "resource_name": resource_name or None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--localization-root",
        type=Path,
        default=DEFAULT_LOCALIZATION_ROOT,
        help="Root containing the game Simplified Chinese localization folders.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "catalog",
        help="Directory for generated catalog files.",
    )
    args = parser.parse_args()

    localization_root = args.localization_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    game_npcs, game_resources = load_optional_game_metadata(output_dir)

    locres_paths: list[tuple[str, Path]] = []
    for domain, relative_path in DEFAULT_LOCRES_FILES:
        path = localization_root / relative_path
        if not path.is_file():
            raise FileNotFoundError(f"Missing localization resource: {path}")
        locres_paths.append((domain, path))

    npc_metadata: dict[str, dict[str, str]] = defaultdict(dict)
    raw_records: list[dict[str, str]] = []
    unparsed_dialogue_like: list[dict[str, str]] = []
    entry_counts: Counter[str] = Counter()

    for domain, path in locres_paths:
        for namespace, key, translation in iter_locres_entries(path):
            entry_counts[f"{domain}:{namespace}"] += 1

            if domain == "npc" and namespace == "NPCs":
                metadata_match = NPC_METADATA_RE.match(key)
                if metadata_match:
                    npc_id = metadata_match.group("npc_id")
                    field = metadata_match.group("field").lower()
                    cleaned = (
                        compact_description(translation)
                        if field == "description"
                        else normalize_text(translation)
                    )
                    npc_metadata[npc_id][field] = cleaned

            if (domain, namespace) in NARRATION_NAMESPACES:
                text = normalize_text(translation)
                raw_records.append(
                    {
                        "speaker_id": NARRATOR_CHARACTER_ID,
                        "speaker": NARRATOR_NAME,
                        "text": text,
                        "raw_text": translation.strip(),
                        "source": source_info(domain, namespace, key),
                    }
                )
                continue

            if (domain, namespace, key) in SYSTEM_NARRATION_KEYS:
                text = normalize_text(translation)
                raw_records.append(
                    {
                        "speaker_id": NARRATOR_CHARACTER_ID,
                        "speaker": NARRATOR_NAME,
                        "text": text,
                        "raw_text": translation.strip(),
                        "source": source_info(domain, namespace, key),
                    }
                )
                continue

            if "$@$" not in translation:
                continue

            match = DIALOGUE_RE.match(translation)
            if not match:
                unparsed_dialogue_like.append(
                    {
                        **source_info(domain, namespace, key),
                        "translation": translation,
                    }
                )
                continue

            speaker_id = match.group("speaker_id")
            speaker = normalize_text(match.group("speaker"))
            text = normalize_text(match.group("text"))
            raw_records.append(
                {
                    "speaker_id": speaker_id,
                    "speaker": speaker,
                    "text": text,
                    "raw_text": match.group("text").strip(),
                    "source": source_info(domain, namespace, key),
                }
            )

    # A few shipped dialogue rows omit the display name while retaining a
    # stable NPC id. Resolve those rows from official NPC metadata when
    # possible. Truly anonymous rows remain blank and are matched at runtime
    # as anonymous dialogue without being assigned the narrator's voice.
    resolved_blank_speakers = 0
    for record in raw_records:
        if record["speaker"]:
            continue
        character_id = record["speaker_id"]
        resolved_name = (
            npc_metadata.get(character_id, {}).get("name", "")
            or game_npcs.get(character_id, {}).get("name", "")
        )
        if resolved_name:
            record["speaker"] = normalize_text(resolved_name)
            resolved_blank_speakers += 1

    # A line can be reused by multiple quest rows. Generate it only once while
    # retaining all sources so catalog problems remain traceable.
    grouped_lines: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in raw_records:
        identity = (record["speaker_id"], record["speaker"], record["text"])
        if identity not in grouped_lines:
            digest_source = "\0".join(identity).encode("utf-8")
            grouped_lines[identity] = {
                "line_id": hashlib.sha256(digest_source).hexdigest()[:20],
                "speaker_id": record["speaker_id"],
                "speaker": record["speaker"],
                "text": record["text"],
                "raw_text": record["raw_text"],
                "speakable": bool(record["text"].strip()),
                "nonverbal": bool(record["text"].strip())
                and not bool(SPEAKABLE_RE.search(record["text"])),
                "sources": [],
            }
        source = record["source"]
        if source not in grouped_lines[identity]["sources"]:
            grouped_lines[identity]["sources"].append(source)

    lines = sorted(
        grouped_lines.values(),
        key=lambda item: (
            numeric_sort_key(item["speaker_id"]),
            item["speaker"],
            item["text"],
        ),
    )

    speaker_names: dict[str, Counter[str]] = defaultdict(Counter)
    speaker_samples: dict[str, list[str]] = defaultdict(list)
    speaker_line_counts: Counter[str] = Counter()
    speaker_speakable_counts: Counter[str] = Counter()
    for line in lines:
        speaker_id = line["speaker_id"]
        speaker_names[speaker_id][line["speaker"]] += len(line["sources"])
        speaker_line_counts[speaker_id] += 1
        if line["speakable"]:
            speaker_speakable_counts[speaker_id] += 1
            if not line["nonverbal"] and len(speaker_samples[speaker_id]) < 5:
                speaker_samples[speaker_id].append(line["text"])

    dialogue_speaker_ids = set(speaker_names)
    description_by_name: dict[str, set[str]] = defaultdict(set)
    for row in game_npcs.values():
        if row.get("name") and row.get("description"):
            description_by_name[row["name"]].add(row["description"])

    all_character_ids = set(npc_metadata) | dialogue_speaker_ids
    characters: list[dict[str, Any]] = []
    for character_id in sorted(all_character_ids, key=numeric_sort_key):
        character_speaker_names = speaker_names.get(character_id, Counter())
        aliases = [
            name for name, _ in character_speaker_names.most_common() if name
        ]
        game_npc = game_npcs.get(character_id, {})
        localized_name = npc_metadata.get(character_id, {}).get("name", "")
        preferred_name = (
            localized_name or game_npc.get("name", "") or (aliases[0] if aliases else "")
        )
        all_names = []
        for name in [preferred_name, *aliases]:
            if name and name not in all_names:
                all_names.append(name)

        description = (
            npc_metadata.get(character_id, {}).get("description", "")
            or game_npc.get("description", "")
        )
        description_source = "direct"
        if not description and preferred_name:
            inherited_candidates = description_by_name.get(preferred_name, set())
            if len(inherited_candidates) == 1:
                description = next(iter(inherited_candidates))
                description_source = "same_game_name_variant"
        if not description:
            description_source = "missing"

        resource_name = game_npc.get("resource_name", "")
        resource = game_resources.get(resource_name, {})
        assets = resource.get("assets", {})
        has_asset_metadata = bool(resource)
        voice_traits = infer_voice_traits(
            preferred_name, description, resource_name, assets
        )
        line_count = speaker_line_counts.get(character_id, 0)
        speakable_count = speaker_speakable_counts.get(character_id, 0)
        if not line_count:
            classification_status = "no_dialogue"
        elif voice_traits["role"] in {"narrator", "nonhuman"}:
            classification_status = "classified_from_game_data"
        elif voice_traits["gender"] and voice_traits["age_group"]:
            classification_status = "classified_from_official_description"
        elif assets.get("DlgImage") or assets.get("HeadImage"):
            classification_status = "needs_ai_profile"
        else:
            classification_status = "needs_manual_review"

        characters.append(
            {
                "character_id": character_id,
                "name": preferred_name,
                "aliases": all_names,
                "description": description,
                "description_source": description_source,
                "dialogue_line_count": line_count,
                "speakable_line_count": speakable_count,
                "sample_lines": speaker_samples.get(character_id, []),
                "game_data": {
                    "resource_name": resource_name or None,
                    "function_name": game_npc.get("function_name") or None,
                    "function_type": game_npc.get("function_type") or None,
                    "guild_id": game_npc.get("guild_id"),
                    "level": game_npc.get("level"),
                    "hobbies": game_npc.get("hobbies", []),
                    "weapon_limits": game_npc.get("weapon_limits", []),
                },
                "asset_metadata": {
                    "resource_id": resource_name or None,
                    "character_asset": assets.get("IdleDownFlipbook") or None,
                    "portrait_asset": (
                        assets.get("DlgImage") or assets.get("HeadImage") or None
                    ),
                    "head_sprite_asset": assets.get("HeadSprite") or None,
                    "model_asset": None,
                    "loaded": has_asset_metadata,
                },
                "voice_traits": voice_traits,
                "classification_status": classification_status,
            }
        )

    dialogue_path = output_dir / "dialogue_catalog.jsonl"
    with dialogue_path.open("w", encoding="utf-8", newline="\n") as handle:
        for line in lines:
            handle.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")

    unparsed_path = output_dir / "unparsed_dialogue_like.jsonl"
    with unparsed_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in unparsed_dialogue_like:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")

    write_json(output_dir / "character_registry.json", characters)

    with (output_dir / "character_registry.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "character_id",
                "name",
                "aliases",
                "description",
                "dialogue_line_count",
                "speakable_line_count",
                "classification_status",
            ),
        )
        writer.writeheader()
        for character in characters:
            writer.writerow(
                {
                    "character_id": character["character_id"],
                    "name": character["name"],
                    "aliases": " | ".join(character["aliases"]),
                    "description": character["description"],
                    "dialogue_line_count": character["dialogue_line_count"],
                    "speakable_line_count": character["speakable_line_count"],
                    "classification_status": character["classification_status"],
                }
            )

    duplicate_count = len(raw_records) - len(lines)
    stats = {
        "localization_root": str(localization_root),
        "output_dir": str(output_dir),
        "localization_entry_counts": dict(sorted(entry_counts.items())),
        "parsed_dialogue_source_entries": len(raw_records),
        "unique_dialogue_lines": len(lines),
        "duplicate_source_entries_collapsed": duplicate_count,
        "speakable_unique_lines": sum(1 for line in lines if line["speakable"]),
        "punctuation_only_unique_lines": sum(
            1 for line in lines if line["nonverbal"]
        ),
        "empty_unique_lines": sum(1 for line in lines if not line["speakable"]),
        "unparsed_dialogue_like_entries": len(unparsed_dialogue_like),
        "narration_source_entries": sum(
            1 for record in raw_records if record["speaker_id"] == NARRATOR_CHARACTER_ID
        ),
        "resolved_blank_speakers": resolved_blank_speakers,
        "speaker_ids_with_dialogue": len(dialogue_speaker_ids),
        "named_npc_records": sum(
            1 for metadata in npc_metadata.values() if metadata.get("name")
        ),
        "npc_records_with_description": sum(
            1 for metadata in npc_metadata.values() if metadata.get("description")
        ),
        "character_registry_records": len(characters),
        "speaker_ids_with_multiple_display_names": sum(
            1 for speaker_id in dialogue_speaker_ids if len(speaker_names[speaker_id]) > 1
        ),
        "dialogue_speakers_with_game_npc_row": sum(
            1 for speaker_id in dialogue_speaker_ids if speaker_id in game_npcs
        ),
        "dialogue_speakers_with_resource_metadata": sum(
            1
            for character in characters
            if character["dialogue_line_count"]
            and character["asset_metadata"]["loaded"]
        ),
        "dialogue_speakers_with_description": sum(
            1
            for character in characters
            if character["dialogue_line_count"] and character["description"]
        ),
        "dialogue_speakers_needing_ai_profile": sum(
            1
            for character in characters
            if character["classification_status"] == "needs_ai_profile"
        ),
    }
    write_json(output_dir / "catalog_stats.json", stats)

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
