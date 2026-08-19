#!/usr/bin/env python3
"""Resolve DeepSeek profiles into consistent, generation-ready voice groups."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = PROJECT_ROOT / "catalog"
PORTRAIT_REVIEW_PATH = (
    CATALOG_DIR / "portraits" / "portrait_demographic_review.json"
)
PORTRAIT_ASSET_REVIEW_PATH = (
    CATALOG_DIR / "portraits" / "portrait_demographics_by_asset.json"
)
PROFILE_OVERRIDE_PATH = (
    CATALOG_DIR / "profiles" / "profile_demographic_overrides.json"
)

GENERIC_NAME_RE = re.compile(
    r"(?:村民|百姓|路人|客人|弟子|门人|守卫|侍卫|士兵|官兵|山贼|强盗|喽啰|"
    r"黑衣人|蒙面人|商人|商贩|货郎|伙计|小二|掌柜|铁匠|裁缝|郎中|大夫|"
    r"船夫|渔夫|农夫|僧人|和尚|道士|家丁|仆人|侍女|丫鬟|？？？|\?\?\?)"
)
GENERIC_SURNAME_NAME_RE = re.compile(r"^.{1,3}氏$")

AGE_LABELS = {
    "child": "儿童",
    "teen": "少年",
    "young_adult": "青年",
    "adult": "成年",
    "middle_aged": "中年",
    "elderly": "老年",
    "ageless": "超越常人年龄感的",
}
GENDER_LABELS = {"male": "男性", "female": "女性"}

PROMPT_GENDER_PATTERNS = {
    "male": re.compile(r"男性|男子|男声|男孩|男童"),
    "female": re.compile(r"女性|女子|女声|女孩|女童|少女"),
}
PROMPT_AGE_PATTERNS = (
    ("child", re.compile(r"儿童|孩童|幼童|男童|女童|小男孩|小女孩")),
    ("elderly", re.compile(r"老年|老人|老者|老翁|老妇|年迈|高龄")),
    ("middle_aged", re.compile(r"中年|壮年")),
    ("teen", re.compile(r"少年|少女")),
    ("young_adult", re.compile(r"青年|年轻")),
    ("adult", re.compile(r"成年")),
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def numeric_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


def add_resolution(
    profile: dict[str, Any],
    field: str,
    value: str,
    source: str,
    fact: str,
) -> bool:
    current = profile.get(field, "unknown")
    if current not in {None, "", "unknown"}:
        return False
    profile[field] = value
    profile.setdefault("resolution_evidence", []).append(
        {"field": field, "source": source, "fact": fact}
    )
    return True


def known_values(items: list[dict[str, Any]], field: str) -> set[str]:
    return {
        str(item.get(field))
        for item in items
        if item.get(field) not in {None, "", "unknown"}
    }


def infer_explicit_prompt_demographics(profile: dict[str, Any]) -> dict[str, str]:
    """Read only explicit demographic wording from the AI voice direction.

    DeepSeek often described a character as e.g. ``中年男性`` while leaving the
    corresponding structured field as ``unknown``.  Those literal statements
    are evidence, not name-based guesses.  A plain ``成年`` remains its own
    broad category instead of being arbitrarily coerced to young or middle age.
    """

    prompt = str((profile.get("voice") or {}).get("voice_design_prompt") or "")
    if not prompt:
        return {}
    if "非人类" in prompt:
        return {"gender": "nonhuman", "age_group": "ageless"}

    facts: dict[str, str] = {}
    gender_matches = [
        value for value, pattern in PROMPT_GENDER_PATTERNS.items() if pattern.search(prompt)
    ]
    if len(gender_matches) == 1:
        facts["gender"] = gender_matches[0]
    for value, pattern in PROMPT_AGE_PATTERNS:
        if pattern.search(prompt):
            facts["age_group"] = value
            break
    return facts


def load_portrait_review() -> dict[str, dict[str, Any]]:
    """Resolve human review-sheet indices back to stable asset references."""

    if PORTRAIT_ASSET_REVIEW_PATH.is_file():
        document = read_json(PORTRAIT_ASSET_REVIEW_PATH)
        return {
            str(asset): dict(values)
            for asset, values in (document.get("by_asset") or {}).items()
        }
    if not PORTRAIT_REVIEW_PATH.is_file():
        return {}
    review = read_json(PORTRAIT_REVIEW_PATH)
    index_path = PORTRAIT_REVIEW_PATH.parent / review["index_source"]
    index_records = read_json(index_path)
    asset_by_index = {
        int(item["review_index"]): item["asset_reference"] for item in index_records
    }
    output: dict[str, dict[str, Any]] = defaultdict(dict)
    for output_field, source_key in (
        ("gender", "gender_by_index"),
        ("age_group", "age_by_index"),
    ):
        seen: set[int] = set()
        for value, indices in (review.get(source_key) or {}).items():
            for raw_index in indices:
                review_index = int(raw_index)
                if review_index in seen:
                    raise ValueError(
                        f"Duplicate portrait review index {review_index} in {source_key}"
                    )
                seen.add(review_index)
                if review_index not in asset_by_index:
                    raise ValueError(f"Unknown portrait review index {review_index}")
                output[asset_by_index[review_index]][output_field] = value
        if seen != set(asset_by_index):
            missing = sorted(set(asset_by_index) - seen)
            raise ValueError(f"Unclassified portrait indices in {source_key}: {missing}")

    for field, indices in (review.get("override_existing_ai_by_index") or {}).items():
        for raw_index in indices:
            review_index = int(raw_index)
            output[asset_by_index[review_index]].setdefault("override_fields", []).append(
                field
            )
    return dict(output)


def voice_group_key(
    profile: dict[str, Any],
    character: dict[str, Any],
    split_named_variants: set[str],
) -> str:
    name = (profile.get("name") or character.get("name") or "").strip()
    resource = (character.get("game_data") or {}).get("resource_name") or ""
    if not name:
        return f"id:{profile['character_id']}"
    if (
        GENERIC_NAME_RE.search(name)
        or GENERIC_SURNAME_NAME_RE.fullmatch(name)
        or name in split_named_variants
    ):
        return f"generic:{name}|resource:{resource or profile['character_id']}"
    return f"named:{name}"


def main() -> int:
    registry_path = CATALOG_DIR / "character_registry.json"
    profiles_path = CATALOG_DIR / "profiles" / "character_profiles.json"
    registry = read_json(registry_path)
    original_profiles = read_json(profiles_path)
    registry_by_id = {item["character_id"]: item for item in registry}
    profiles = {
        item["character_id"]: deepcopy(item) for item in original_profiles
    }

    conflicts: list[dict[str, Any]] = []
    deterministic_fills: Counter[str] = Counter()
    for character_id, profile in profiles.items():
        character = registry_by_id.get(character_id, {})
        deterministic = character.get("voice_traits") or {}
        for field in ("gender", "age_group"):
            direct = deterministic.get(field)
            ai_value = profile.get(field)
            if direct and ai_value not in {None, "", "unknown", direct}:
                conflicts.append(
                    {
                        "kind": "official_vs_ai",
                        "character_id": character_id,
                        "name": profile.get("name", ""),
                        "field": field,
                        "official_value": direct,
                        "ai_value": ai_value,
                        "resolution": direct,
                    }
                )
                profile[field] = direct
            elif direct and add_resolution(
                profile,
                field,
                direct,
                "official_game_data",
                "NPC 表人物描述或角色资源的确定性分类",
            ):
                deterministic_fills[field] += 1

    prompt_fills: Counter[str] = Counter()
    for profile in profiles.values():
        for field, value in infer_explicit_prompt_demographics(profile).items():
            if add_resolution(
                profile,
                field,
                value,
                "explicit_ai_voice_direction",
                f"角色画像的声线指令明确写明 {field}={value}",
            ):
                prompt_fills[field] += 1

    # Game portraits are stronger evidence for visible demographics than an
    # LLM inference. By default they fill unknowns only; a tiny explicit list
    # corrects visually obvious AI mistakes while official descriptions remain
    # protected.
    portrait_review = load_portrait_review()
    portrait_fills: Counter[str] = Counter()
    portrait_overrides: Counter[str] = Counter()
    for character_id, profile in profiles.items():
        character = registry_by_id.get(character_id, {})
        deterministic = character.get("voice_traits") or {}
        portrait_asset = (character.get("asset_metadata") or {}).get(
            "portrait_asset"
        )
        reviewed = portrait_review.get(portrait_asset or "") or {}
        for field in ("gender", "age_group"):
            reviewed_value = reviewed.get(field)
            if reviewed_value in {None, "", "unknown"}:
                continue
            official_value = deterministic.get(field)
            current = profile.get(field, "unknown")
            if official_value and official_value != reviewed_value:
                conflicts.append(
                    {
                        "kind": "official_vs_portrait",
                        "character_id": character_id,
                        "name": profile.get("name", ""),
                        "field": field,
                        "official_value": official_value,
                        "portrait_value": reviewed_value,
                        "resolution": official_value,
                    }
                )
                continue
            if add_resolution(
                profile,
                field,
                reviewed_value,
                "game_portrait_visual_review",
                f"游戏原始头像的可见{field}分类为 {reviewed_value}",
            ):
                portrait_fills[field] += 1
            elif (
                field in reviewed.get("override_fields", [])
                and current != reviewed_value
                and not official_value
            ):
                conflicts.append(
                    {
                        "kind": "portrait_vs_ai",
                        "character_id": character_id,
                        "name": profile.get("name", ""),
                        "field": field,
                        "ai_value": current,
                        "portrait_value": reviewed_value,
                        "resolution": reviewed_value,
                    }
                )
                profile[field] = reviewed_value
                profile.setdefault("resolution_evidence", []).append(
                    {
                        "field": field,
                        "source": "game_portrait_visual_review",
                        "fact": f"游戏原始头像清晰显示为 {reviewed_value}",
                    }
                )
                portrait_overrides[field] += 1

    profile_override_document = (
        read_json(PROFILE_OVERRIDE_PATH) if PROFILE_OVERRIDE_PATH.is_file() else {}
    )
    profile_override_fills: Counter[str] = Counter()
    profile_override_replacements: Counter[str] = Counter()
    explicit_replacements = {
        field: {str(item) for item in character_ids}
        for field, character_ids in (
            profile_override_document.get("override_existing_fields") or {}
        ).items()
    }
    for character_id, values in (
        profile_override_document.get("overrides") or {}
    ).items():
        profile = profiles.get(str(character_id))
        if not profile:
            raise ValueError(f"Unknown profile override character: {character_id}")
        reason = values.get("reason", "人工复核后的角色设定")
        for field in ("gender", "age_group"):
            value = values.get(field)
            if value in {None, "", "unknown"}:
                continue
            if add_resolution(
                profile,
                field,
                value,
                "dialogue_background_review",
                reason,
            ):
                profile_override_fills[field] += 1
            elif str(character_id) in explicit_replacements.get(field, set()):
                current = profile.get(field)
                if current != value:
                    conflicts.append(
                        {
                            "kind": "manual_review_vs_ai",
                            "character_id": str(character_id),
                            "name": profile.get("name", ""),
                            "field": field,
                            "ai_value": current,
                            "reviewed_value": value,
                            "resolution": value,
                        }
                    )
                    profile[field] = value
                    profile.setdefault("resolution_evidence", []).append(
                        {
                            "field": field,
                            "source": "dialogue_background_review",
                            "fact": reason,
                        }
                    )
                    profile_override_replacements[field] += 1

    neutral_fallback_character_ids = {
        str(item)
        for item in profile_override_document.get(
            "neutral_fallback_character_ids", []
        )
    }

    # A resource row denotes the exact portrait/sprite set. If every known NPC
    # using that visual resource agrees, it is stronger evidence than a name.
    by_resource: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for character_id, profile in profiles.items():
        character = registry_by_id.get(character_id, {})
        resource = (character.get("game_data") or {}).get("resource_name")
        if resource:
            by_resource[resource].append(profile)

    resource_fills: Counter[str] = Counter()
    resource_broad_age_refinements = 0
    for resource_name, members in by_resource.items():
        age_values = known_values(members, "age_group")
        specific_ages = age_values - {"adult"}
        if "adult" in age_values and len(specific_ages) == 1:
            refined_age = next(iter(specific_ages))
            for profile in members:
                if profile.get("age_group") == "adult":
                    profile["age_group"] = refined_age
                    profile.setdefault("resolution_evidence", []).append(
                        {
                            "field": "age_group",
                            "source": "shared_visual_resource",
                            "fact": (
                                f"宽泛成年档依据同一角色资源 {resource_name} "
                                f"的明确年龄细化为 {refined_age}"
                            ),
                        }
                    )
                    resource_broad_age_refinements += 1
        for field in ("gender", "age_group"):
            values = known_values(members, field)
            if len(values) != 1:
                continue
            consensus = next(iter(values))
            for profile in members:
                if add_resolution(
                    profile,
                    field,
                    consensus,
                    "shared_visual_resource",
                    f"同一角色资源 {resource_name} 的已知角色一致为 {consensus}",
                ):
                    resource_fills[field] += 1

    # Exact display-name variants often represent the same named story
    # character at different quest states. Use this only after visual consensus.
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for profile in profiles.values():
        if profile.get("name"):
            by_name[profile["name"]].append(profile)

    name_fills: Counter[str] = Counter()
    name_broad_age_refinements = 0
    for name, members in by_name.items():
        age_values = known_values(members, "age_group")
        specific_ages = age_values - {"adult"}
        if "adult" in age_values and len(specific_ages) == 1:
            refined_age = next(iter(specific_ages))
            for profile in members:
                if profile.get("age_group") == "adult":
                    profile["age_group"] = refined_age
                    profile.setdefault("resolution_evidence", []).append(
                        {
                            "field": "age_group",
                            "source": "same_display_name_variant",
                            "fact": (
                                f"宽泛成年档依据同名剧情变体 {name} "
                                f"的明确年龄细化为 {refined_age}"
                            ),
                        }
                    )
                    name_broad_age_refinements += 1
        for field in ("gender", "age_group"):
            values = known_values(members, field)
            if len(values) != 1:
                if len(values) > 1:
                    conflicts.append(
                        {
                            "kind": "same_name_conflict",
                            "name": name,
                            "field": field,
                            "values": sorted(values),
                            "character_ids": [item["character_id"] for item in members],
                        }
                    )
                continue
            consensus = next(iter(values))
            for profile in members:
                if add_resolution(
                    profile,
                    field,
                    consensus,
                    "same_display_name_variant",
                    f"同名剧情变体 {name} 的已知档案一致为 {consensus}",
                ):
                    name_fills[field] += 1

    # Ensure the final VoiceDesign instruction explicitly contains resolved
    # demographic information. This first sentence is authoritative when an
    # older AI prompt contains a contradictory incidental age word.
    for profile in profiles.values():
        voice = profile.get("voice") or {}
        prompt = voice.get("voice_design_prompt", "").strip()
        gender = profile.get("gender")
        age = profile.get("age_group")
        demographic = "".join(
            part
            for part in (AGE_LABELS.get(age, ""), GENDER_LABELS.get(gender, ""))
            if part
        )
        if demographic:
            voice["voice_design_prompt"] = (
                f"确定人物设定：{demographic}角色，声线必须保持这一年龄与性别呈现。"
                + prompt
            )
        profile["voice"] = voice

    # Exact names normally share a voice across quest-state variants. When the
    # resolved portraits show different demographics (child/adult, different
    # people called 王氏, etc.), split by the game's visual resource instead.
    split_named_variants: set[str] = set()
    for name, members in by_name.items():
        if len(known_values(members, "gender")) > 1 or len(
            known_values(members, "age_group")
        ) > 1:
            split_named_variants.add(name)

    groups: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for character_id, profile in profiles.items():
        character = registry_by_id.get(character_id, {})
        groups[
            voice_group_key(profile, character, split_named_variants)
        ].append((profile, character))

    voice_plan: list[dict[str, Any]] = []
    for raw_group_key, members in groups.items():
        members.sort(
            key=lambda pair: (
                -int(pair[1].get("speakable_line_count", 0)),
                -float(pair[0].get("confidence", 0.0)),
                numeric_key(pair[0]["character_id"]),
            )
        )
        representative_profile, representative_character = members[0]
        group_id = hashlib.sha256(raw_group_key.encode("utf-8")).hexdigest()[:16]
        genders = known_values([pair[0] for pair in members], "gender")
        ages = known_values([pair[0] for pair in members], "age_group")
        group_conflict = len(genders) > 1 or len(ages) > 1
        gender = next(iter(genders)) if len(genders) == 1 else "unknown"
        age_group = next(iter(ages)) if len(ages) == 1 else "unknown"
        role = representative_profile.get("role", "")
        member_ids = [pair[0]["character_id"] for pair in members]
        neutral_fallback = bool(member_ids) and set(member_ids).issubset(
            neutral_fallback_character_ids
        )
        special_ready = (
            role == "narrator" or gender == "nonhuman" or neutral_fallback
        )
        review_reasons: list[str] = []
        if group_conflict:
            review_reasons.append("member_demographic_conflict")
        if gender == "unknown" and not special_ready:
            review_reasons.append("unknown_gender")
        if age_group == "unknown" and not special_ready:
            review_reasons.append("unknown_age")
        warnings: list[str] = []
        if neutral_fallback:
            warnings.append("demographic_unknown_neutral_voice")
        if float(representative_profile.get("confidence", 0.0)) < 0.45:
            warnings.append("low_ai_confidence")

        voice_plan.append(
            {
                "voice_group_id": group_id,
                "group_key": raw_group_key,
                "name": representative_profile.get("name", ""),
                "member_character_ids": member_ids,
                "resource_names": sorted(
                    {
                        (pair[1].get("game_data") or {}).get("resource_name")
                        for pair in members
                        if (pair[1].get("game_data") or {}).get("resource_name")
                    }
                ),
                "portrait_assets": sorted(
                    {
                        (pair[1].get("asset_metadata") or {}).get("portrait_asset")
                        for pair in members
                        if (pair[1].get("asset_metadata") or {}).get("portrait_asset")
                    }
                ),
                "gender": gender,
                "age_group": age_group,
                "role": role,
                "personality_traits": representative_profile.get(
                    "personality_traits", []
                ),
                "speaking_style": representative_profile.get("speaking_style", ""),
                "voice": representative_profile.get("voice", {}),
                "confidence": representative_profile.get("confidence", 0.0),
                "evidence": representative_profile.get("evidence", []),
                "resolution_evidence": representative_profile.get(
                    "resolution_evidence", []
                ),
                "speakable_line_count": sum(
                    int(pair[1].get("speakable_line_count", 0)) for pair in members
                ),
                "review_reasons": review_reasons,
                "warnings": warnings,
                "generation_status": "needs_review" if review_reasons else "ready",
            }
        )

    voice_plan.sort(
        key=lambda item: (-item["speakable_line_count"], item["name"], item["voice_group_id"])
    )
    review_queue = [
        item for item in voice_plan if item["generation_status"] == "needs_review"
    ]
    resolved_profiles = sorted(
        profiles.values(), key=lambda item: numeric_key(item["character_id"])
    )

    original_gender = Counter(item["gender"] for item in original_profiles)
    original_age = Counter(item["age_group"] for item in original_profiles)
    resolved_gender = Counter(item["gender"] for item in resolved_profiles)
    resolved_age = Counter(item["age_group"] for item in resolved_profiles)
    stats = {
        "profiles": len(resolved_profiles),
        "voice_groups": len(voice_plan),
        "voice_groups_ready": sum(
            item["generation_status"] == "ready" for item in voice_plan
        ),
        "voice_groups_needing_review": len(review_queue),
        "speakable_lines": sum(item["speakable_line_count"] for item in voice_plan),
        "original_gender_counts": dict(original_gender),
        "resolved_gender_counts": dict(resolved_gender),
        "original_age_counts": dict(original_age),
        "resolved_age_counts": dict(resolved_age),
        "deterministic_fills": dict(deterministic_fills),
        "explicit_prompt_fills": dict(prompt_fills),
        "portrait_review_assets": len(portrait_review),
        "portrait_fills": dict(portrait_fills),
        "portrait_ai_overrides": dict(portrait_overrides),
        "profile_override_fills": dict(profile_override_fills),
        "profile_override_replacements": dict(profile_override_replacements),
        "neutral_fallback_profiles": len(neutral_fallback_character_ids),
        "split_named_variants": len(split_named_variants),
        "shared_resource_fills": dict(resource_fills),
        "shared_resource_broad_age_refinements": resource_broad_age_refinements,
        "same_name_fills": dict(name_fills),
        "same_name_broad_age_refinements": name_broad_age_refinements,
        "conflicts": len(conflicts),
    }

    write_json(CATALOG_DIR / "profiles" / "character_profiles_resolved.json", resolved_profiles)
    write_json(CATALOG_DIR / "voice_plan.json", voice_plan)
    write_json(CATALOG_DIR / "profile_review_queue.json", review_queue)
    write_json(CATALOG_DIR / "profile_conflicts.json", conflicts)
    write_json(CATALOG_DIR / "voice_plan_stats.json", stats)

    with (CATALOG_DIR / "voice_plan.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "voice_group_id",
                "name",
                "member_character_ids",
                "gender",
                "age_group",
                "role",
                "personality_traits",
                "speakable_line_count",
                "generation_status",
                "review_reasons",
                "voice_design_prompt",
            ),
        )
        writer.writeheader()
        for item in voice_plan:
            writer.writerow(
                {
                    "voice_group_id": item["voice_group_id"],
                    "name": item["name"],
                    "member_character_ids": " | ".join(item["member_character_ids"]),
                    "gender": item["gender"],
                    "age_group": item["age_group"],
                    "role": item["role"],
                    "personality_traits": " | ".join(item["personality_traits"]),
                    "speakable_line_count": item["speakable_line_count"],
                    "generation_status": item["generation_status"],
                    "review_reasons": " | ".join(item["review_reasons"]),
                    "voice_design_prompt": item["voice"].get(
                        "voice_design_prompt", ""
                    ),
                }
            )

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
