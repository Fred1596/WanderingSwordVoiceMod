#!/usr/bin/env python3
"""Build resumable anchor, dialogue-audio, and runtime lookup manifests."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = PROJECT_ROOT / "catalog"
OFFLINE_DIR = PROJECT_ROOT / "offline"
MANIFEST_DIR = OFFLINE_DIR / "manifest"

PLACEHOLDER_WARNING_RE = re.compile(r"\s*[（(]当前\{[^}]+\}任务未完成.*$", re.DOTALL)
UNSUITABLE_ANCHOR_RE = re.compile(r"\{[^}]+\}|【|】|（当前|\n")
EMOTIONAL_RE = re.compile(r"[！!?！？]{2,}|哈哈|呜呜|救命|去死|该死")
NONVERBAL_RE = re.compile(r"^[\s…！？!?（）()。．，、—\-.]+$")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")


def normalize_runtime(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def normalize_runtime_compat(value: str) -> str:
    """Compatibility form for full-width punctuation and legacy UI variants."""

    return normalize_runtime(unicodedata.normalize("NFKC", value))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def make_tts_text(text: str) -> tuple[str, str]:
    cleaned = PLACEHOLDER_WARNING_RE.sub("", text).strip()
    if not cleaned or not NONVERBAL_RE.fullmatch(cleaned):
        return cleaned, "dialogue_text"
    has_question = "？" in cleaned or "?" in cleaned
    has_exclamation = "！" in cleaned or "!" in cleaned
    if has_question and has_exclamation:
        return "什么？！", "nonverbal_vocalization"
    if has_question:
        return "嗯？", "nonverbal_vocalization"
    if has_exclamation and "…" in cleaned:
        return "啊……！", "nonverbal_vocalization"
    if has_exclamation:
        return "啊！", "nonverbal_vocalization"
    return "嗯……", "nonverbal_vocalization"


def anchor_score(text: str) -> tuple[int, int, str]:
    length = len(text)
    penalty = abs(length - 34)
    if not 20 <= length <= 58:
        penalty += 100
    if UNSUITABLE_ANCHOR_RE.search(text):
        penalty += 200
    if EMOTIONAL_RE.search(text):
        penalty += 30
    penalty += text.count("……") * 8
    return penalty, length, text


def fallback_anchor_text(group: dict[str, Any]) -> str:
    gender = group.get("gender")
    age = group.get("age_group")
    role = group.get("role") or "江湖中人"
    if role == "narrator":
        return "暮色渐沉，远处风声掠过竹林，江湖中的故事仍在悄然继续。"
    if gender == "female" and age in {"child", "teen"}:
        return "前面的路虽然陌生，我会认真听大家的话，也会努力照顾好自己。"
    if gender == "female" and age == "elderly":
        return "江湖路远，凡事都要多留几分心，平平安安才是最要紧的。"
    if gender == "female":
        return "事情既然已经说定，我便会认真办好，也请诸位一路多加小心。"
    if gender == "male" and age in {"child", "teen", "young_adult"}:
        return "江湖路远，只要认准了该做的事，我便会一步一步认真走下去。"
    if gender == "male" and age == "elderly":
        return "年轻人不必心急，先把眼前的事情看清楚，再作打算也不迟。"
    if gender == "male":
        return f"身为{role}，遇事更该沉住气，先看清局势，再决定下一步。"
    if gender == "nonhuman":
        return "风声从远处传来，沉睡的意识缓缓苏醒，发出低沉而奇异的回应。"
    return "眼前局势尚不明朗，先冷静观察片刻，再决定接下来该怎么做。"


def main() -> int:
    voice_plan = read_json(CATALOG_DIR / "voice_plan.json")
    registry = read_json(CATALOG_DIR / "character_registry.json")
    config = read_json(PROJECT_ROOT / "config" / "voice_profiles.json")
    dialogue = [
        item
        for item in iter_jsonl(CATALOG_DIR / "dialogue_catalog.jsonl")
        if item.get("speakable")
    ]

    group_by_character: dict[str, dict[str, Any]] = {}
    for group in voice_plan:
        if group.get("generation_status") != "ready":
            raise ValueError(f"Voice group is not ready: {group['voice_group_id']}")
        for character_id in group["member_character_ids"]:
            if character_id in group_by_character:
                raise ValueError(f"Character assigned to two groups: {character_id}")
            group_by_character[character_id] = group

    runtime_speakers_by_character: dict[str, list[str]] = {}
    for character in registry:
        names: list[str] = []
        for value in [character.get("name", ""), *(character.get("aliases") or [])]:
            normalized = normalize_runtime(str(value or ""))
            if normalized and normalized not in names:
                names.append(normalized)
        runtime_speakers_by_character[str(character["character_id"])] = names

    lines_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in dialogue:
        group = group_by_character.get(line["speaker_id"])
        if group is None:
            raise ValueError(f"No voice group for speaker {line['speaker_id']}")
        lines_by_group[group["voice_group_id"]].append(line)

    existing_profile_for_speaker = config.get("speaker_overrides", {})
    existing_anchor_by_speaker = {
        speaker: config["profiles"][profile_name]
        for speaker, profile_name in existing_profile_for_speaker.items()
    }

    anchors: list[dict[str, Any]] = []
    for group in voice_plan:
        group_id = group["voice_group_id"]
        reused = existing_anchor_by_speaker.get(group.get("name", ""))
        if reused:
            anchor_file = reused["anchor_file"]
            anchor_text = reused["anchor_text"]
            source = "existing_approved_anchor"
        else:
            candidates = [item["text"] for item in lines_by_group[group_id]]
            suitable = [
                text
                for text in candidates
                if 20 <= len(text) <= 58 and not UNSUITABLE_ANCHOR_RE.search(text)
            ]
            anchor_text = (
                min(suitable, key=anchor_score)
                if suitable
                else fallback_anchor_text(group)
            )
            anchor_file = f"offline/anchors/{group_id}.wav"
            source = "representative_game_dialogue" if suitable else "neutral_template"
        anchors.append(
            {
                "voice_group_id": group_id,
                "name": group.get("name", ""),
                "member_character_ids": group["member_character_ids"],
                "gender": group.get("gender", "unknown"),
                "age_group": group.get("age_group", "unknown"),
                "role": group.get("role", ""),
                "voice_design_prompt": group["voice"]["voice_design_prompt"],
                "anchor_text": anchor_text,
                "anchor_file": anchor_file,
                "anchor_text_source": source,
                "reuse_existing": bool(reused),
                "speakable_line_count": group["speakable_line_count"],
            }
        )

    jobs: list[dict[str, Any]] = []
    runtime_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    text_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    prefix_candidates: list[dict[str, Any]] = []
    total_characters = 0
    for line in dialogue:
        group = group_by_character[line["speaker_id"]]
        tts_text, tts_text_strategy = make_tts_text(line["text"])
        job_key = f"{line['speaker_id']}\0{line['text']}"
        job_digest = sha256_text(job_key)
        relative_audio = f"offline/audio/{job_digest[:2]}/{job_digest}.wav"
        runtime_speaker = normalize_runtime(line.get("speaker") or "旁白")
        runtime_text = normalize_runtime(line["text"])
        runtime_key = sha256_text(f"{runtime_speaker}\0{runtime_text}")
        runtime_speaker_aliases: list[str] = []
        for value in [
            runtime_speaker,
            *runtime_speakers_by_character.get(line["speaker_id"], []),
        ]:
            normalized = normalize_runtime(value)
            if normalized and normalized not in runtime_speaker_aliases:
                runtime_speaker_aliases.append(normalized)
        record = {
            "job_id": job_digest,
            "line_id": line["line_id"],
            "speaker_id": line["speaker_id"],
            "speaker": line.get("speaker", ""),
            "runtime_speaker": runtime_speaker,
            "text": line["text"],
            "tts_text": tts_text,
            "tts_text_strategy": tts_text_strategy,
            "voice_group_id": group["voice_group_id"],
            "audio_file": relative_audio,
            "runtime_key": runtime_key,
            "runtime_speaker_aliases": runtime_speaker_aliases,
            "source_occurrences": len(line.get("sources") or []),
        }
        jobs.append(record)
        total_characters += len(tts_text)
        for speaker_alias in runtime_speaker_aliases:
            nfc_form = (normalize_runtime(speaker_alias), runtime_text, "nfc")
            nfkc_form = (
                normalize_runtime_compat(speaker_alias),
                normalize_runtime_compat(runtime_text),
                "nfkc",
            )
            lookup_forms = [nfc_form]
            if nfkc_form[:2] != nfc_form[:2]:
                lookup_forms.append(nfkc_form)
            for lookup_speaker, lookup_text, match_mode in lookup_forms:
                lookup_key = sha256_text(f"{lookup_speaker}\0{lookup_text}")
                candidate = {**record, "match_mode": match_mode}
                runtime_candidates[lookup_key].append(candidate)
        text_forms = [(runtime_text, "nfc")]
        compat_runtime_text = normalize_runtime_compat(runtime_text)
        if compat_runtime_text != runtime_text:
            text_forms.append((compat_runtime_text, "nfkc"))
        for lookup_text, match_mode in text_forms:
            text_candidates[sha256_text(lookup_text)].append(
                {**record, "match_mode": match_mode}
            )
        if PLACEHOLDER_WARNING_RE.search(line["text"]):
            prefix = normalize_runtime(line["text"].split("（当前", 1)[0])
            for speaker_alias in runtime_speaker_aliases:
                prefix_candidates.append(
                    {
                        "speaker": speaker_alias,
                        "text_prefix": prefix,
                        "audio_file": relative_audio,
                        "voice_group_id": group["voice_group_id"],
                    }
                )

    runtime_lookup: dict[str, dict[str, Any]] = {}
    collision_details: list[dict[str, Any]] = []
    for runtime_key, candidates in runtime_candidates.items():
        candidates.sort(
            key=lambda item: (
                -int(item["source_occurrences"]),
                item["speaker_id"],
                item.get("match_mode", "nfc"),
                item["job_id"],
            )
        )
        selected = candidates[0]
        runtime_lookup[runtime_key] = {
            "audio_file": selected["audio_file"],
            "voice_group_id": selected["voice_group_id"],
            "speaker": selected["runtime_speaker"],
            "text": selected["text"],
            "match_mode": selected.get("match_mode", "nfc"),
        }
        distinct_groups = sorted({item["voice_group_id"] for item in candidates})
        if len(distinct_groups) > 1:
            collision_details.append(
                {
                    "runtime_key": runtime_key,
                    "speaker": selected["runtime_speaker"],
                    "text": selected["text"],
                    "selected_group": selected["voice_group_id"],
                    "candidate_groups": distinct_groups,
                    "candidate_speaker_ids": sorted(
                        {item["speaker_id"] for item in candidates}
                    ),
                }
            )

    text_fallback_lookup: dict[str, dict[str, Any]] = {}
    text_fallback_collisions = 0
    for text_key, candidates in text_candidates.items():
        distinct_groups = {item["voice_group_id"] for item in candidates}
        if len(distinct_groups) != 1:
            text_fallback_collisions += 1
            continue
        candidates.sort(
            key=lambda item: (
                -int(item["source_occurrences"]),
                item.get("match_mode", "nfc"),
                item["speaker_id"],
                item["job_id"],
            )
        )
        selected = candidates[0]
        text_fallback_lookup[text_key] = {
            "audio_file": selected["audio_file"],
            "voice_group_id": selected["voice_group_id"],
            "speaker": selected["runtime_speaker"],
            "text": selected["text"],
            "match_mode": selected.get("match_mode", "nfc"),
        }

    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    write_json(MANIFEST_DIR / "voice_anchors.json", anchors)
    write_jsonl(MANIFEST_DIR / "dialogue_jobs.jsonl", jobs)
    write_json(
        MANIFEST_DIR / "runtime_lookup.json",
        {
            "algorithm": (
                "sha256(normalized speaker + NUL + normalized text); "
                "NFC exact keys plus NFKC compatibility aliases; unique-voice "
                "text fallback for UI speaker/content update races"
            ),
            "exact": runtime_lookup,
            "text_fallback": text_fallback_lookup,
            "prefix": prefix_candidates,
        },
    )
    write_json(MANIFEST_DIR / "runtime_collisions.json", collision_details)

    source_counts = Counter(item["anchor_text_source"] for item in anchors)
    stats = {
        "voice_groups": len(anchors),
        "new_voice_anchors": sum(not item["reuse_existing"] for item in anchors),
        "reused_approved_anchors": sum(item["reuse_existing"] for item in anchors),
        "anchor_text_sources": dict(source_counts),
        "dialogue_jobs": len(jobs),
        "tts_characters": total_characters,
        "placeholder_prefix_jobs": len(prefix_candidates),
        "runtime_exact_keys": len(runtime_lookup),
        "runtime_compat_alias_keys": sum(
            value.get("match_mode") == "nfkc" for value in runtime_lookup.values()
        ),
        "runtime_cross_voice_collisions": len(collision_details),
        "runtime_text_fallback_keys": len(text_fallback_lookup),
        "runtime_text_fallback_cross_voice_exclusions": text_fallback_collisions,
        "nonverbal_vocalization_jobs": sum(
            item["tts_text_strategy"] == "nonverbal_vocalization" for item in jobs
        ),
        "estimated_pcm16_gib_from_benchmark": round(
            total_characters * 9943 / (1024**3), 2
        ),
        "outputs": {
            "anchors": str(MANIFEST_DIR / "voice_anchors.json"),
            "dialogue_jobs": str(MANIFEST_DIR / "dialogue_jobs.jsonl"),
            "runtime_lookup": str(MANIFEST_DIR / "runtime_lookup.json"),
            "runtime_collisions": str(MANIFEST_DIR / "runtime_collisions.json"),
        },
    }
    write_json(MANIFEST_DIR / "offline_stats.json", stats)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
