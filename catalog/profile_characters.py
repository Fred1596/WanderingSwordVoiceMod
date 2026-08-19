#!/usr/bin/env python3
"""Create evidence-based, TTS-ready character profiles with DeepSeek.

The script is resumable. Each completed profile is cached separately, so reruns
only submit characters that do not yet have a valid local result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = PROJECT_ROOT / "catalog"
DEFAULT_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"

ALLOWED_GENDERS = {"male", "female", "nonhuman", "unknown"}
ALLOWED_AGES = {
    "child",
    "teen",
    "young_adult",
    "middle_aged",
    "elderly",
    "ageless",
    "unknown",
}
ALLOWED_LEVELS = {"low", "medium", "high"}

SYSTEM_PROMPT = r"""
你是中文武侠游戏的角色设定师和配音导演。任务是依据用户提供的游戏原始数据，
为《逸剑风云诀》角色生成可供 Qwen3-TTS VoiceDesign 使用的人设与声线档案。

要求：
1. 证据优先级：官方人物描述 > 角色台词 > 游戏角色资源路径/身份字段 > 显示名。
2. 不得仅凭姓名猜测性别、年龄或性格。证据不足时填 unknown，并在 uncertainties 说明。
3. 同一个角色的多句台词要综合判断；不要把某一句的临时情绪当成永久性格。
4. voice_design_prompt 必须是自然、明确的中文声音描述，包括性别呈现、年龄感、音高、
   音色、语速、力度、咬字、情绪底色与武侠身份感；不得要求模仿现实人物或知名配音演员。
5. evidence 只写输入中确实存在的简短事实或短语，不虚构剧情。
6. 必须只输出一个合法 JSON 对象，不要 Markdown，不要解释文字。

JSON 输出格式示例：
{
  "profiles": [
    {
      "character_id": "5003",
      "name": "示例角色",
      "is_human": true,
      "gender": "male",
      "age_group": "elderly",
      "role": "武当掌门",
      "personality_traits": ["沉稳", "和善"],
      "speaking_style": "语速从容，措辞端正，带长者的宽厚感",
      "voice": {
        "pitch": "low",
        "pace": "slow",
        "energy": "medium",
        "timbre": "温厚而略带苍老颗粒感",
        "articulation": "清晰稳重",
        "emotional_tone": "平和克制",
        "voice_design_prompt": "一位老年男性武林长者……"
      },
      "confidence": 0.92,
      "evidence": [
        {"source": "official_description", "fact": "处事沉稳有度，颇有长者之风"}
      ],
      "uncertainties": []
    }
  ]
}
""".strip()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def batched(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def load_dialogue_samples(path: Path, maximum: int) -> dict[str, list[str]]:
    by_speaker: dict[str, list[str]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = json.loads(raw_line)
            if not line.get("speakable"):
                continue
            text = line.get("text", "").strip()
            if not text or len(text) > 140:
                continue
            by_speaker.setdefault(line["speaker_id"], []).append(text)

    selected: dict[str, list[str]] = {}
    for speaker_id, lines in by_speaker.items():
        unique = list(dict.fromkeys(lines))
        if len(unique) <= maximum:
            selected[speaker_id] = unique
            continue
        # Cover short, medium, and long utterances rather than taking the first
        # N alphabetically sorted catalog entries.
        ordered = sorted(unique, key=lambda value: (len(value), value))
        indexes = {
            round(index * (len(ordered) - 1) / (maximum - 1))
            for index in range(maximum)
        }
        selected[speaker_id] = [ordered[index] for index in sorted(indexes)]
    return selected


def build_character_input(
    character: dict[str, Any], dialogue_samples: dict[str, list[str]]
) -> dict[str, Any]:
    character_id = character["character_id"]
    return {
        "character_id": character_id,
        "display_name": character.get("name", ""),
        "aliases": character.get("aliases", []),
        "official_description": character.get("description", ""),
        "description_source": character.get("description_source", "missing"),
        "game_data": character.get("game_data", {}),
        "visual_resource": character.get("asset_metadata", {}),
        "deterministic_game_traits": character.get("voice_traits", {}),
        "dialogue_line_count": character.get("dialogue_line_count", 0),
        "representative_dialogue": dialogue_samples.get(character_id, []),
    }


def validate_profile(profile: Any, requested_id: str) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise ValueError(f"Profile {requested_id} is not an object")
    character_id = str(profile.get("character_id", ""))
    if character_id != requested_id:
        raise ValueError(
            f"Profile id mismatch: requested {requested_id}, received {character_id}"
        )
    if profile.get("gender") not in ALLOWED_GENDERS:
        profile["gender"] = "unknown"
    if profile.get("age_group") not in ALLOWED_AGES:
        profile["age_group"] = "unknown"
    voice = profile.get("voice")
    if not isinstance(voice, dict):
        voice = {}
        profile["voice"] = voice
    for field in ("pitch", "pace", "energy"):
        if voice.get(field) not in ALLOWED_LEVELS:
            voice[field] = "medium"
    for field in (
        "timbre",
        "articulation",
        "emotional_tone",
        "voice_design_prompt",
    ):
        if not isinstance(voice.get(field), str):
            voice[field] = ""
    confidence = profile.get("confidence", 0.0)
    try:
        profile["confidence"] = min(1.0, max(0.0, float(confidence)))
    except (TypeError, ValueError):
        profile["confidence"] = 0.0
    for field in ("personality_traits", "evidence", "uncertainties"):
        if not isinstance(profile.get(field), list):
            profile[field] = []
    if not isinstance(profile.get("role"), str):
        profile["role"] = ""
    if not isinstance(profile.get("speaking_style"), str):
        profile["speaking_style"] = ""
    return profile


def request_profiles(
    api_key: str,
    api_url: str,
    model: str,
    character_inputs: list[dict[str, Any]],
    timeout: int,
    max_attempts: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    requested_ids = [str(item["character_id"]) for item in character_inputs]
    user_prompt = (
        "请分析以下角色数据，并严格按 system 中的格式输出 JSON。profiles 必须包含每个"
        " character_id，顺序与输入一致。\n"
        + json.dumps({"characters": character_inputs}, ensure_ascii=False)
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "temperature": 0.15,
        "max_tokens": max(8000, len(character_inputs) * 900),
        "stream": False,
    }
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            api_url,
            data=encoded,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "WanderingSwordVoiceMod/1.0",
            },
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_body = response.read().decode("utf-8")
            envelope = json.loads(response_body)
            content = envelope["choices"][0]["message"].get("content", "")
            if not content:
                raise ValueError("DeepSeek returned empty JSON content")
            parsed = json.loads(content)
            raw_profiles = parsed.get("profiles")
            if not isinstance(raw_profiles, list):
                raise ValueError("DeepSeek JSON has no profiles array")
            by_id = {
                str(profile.get("character_id", "")): profile
                for profile in raw_profiles
                if isinstance(profile, dict)
            }
            missing = [character_id for character_id in requested_ids if character_id not in by_id]
            if missing:
                raise ValueError(f"DeepSeek omitted character ids: {missing}")
            profiles = [
                validate_profile(by_id[character_id], character_id)
                for character_id in requested_ids
            ]
            metadata = {
                "model": model,
                "character_ids": requested_ids,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "usage": envelope.get("usage", {}),
                "request_hash": hashlib.sha256(encoded).hexdigest(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            return profiles, metadata
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")[:2000]
            last_error = RuntimeError(f"HTTP {error.code}: {body}")
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as error:
            last_error = error

        if attempt < max_attempts:
            time.sleep(min(20.0, (2**attempt) + random.random()))

    raise RuntimeError(
        f"DeepSeek batch {requested_ids} failed after {max_attempts} attempts: {last_error}"
    )


def load_cached_profile(path: Path, character_id: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return validate_profile(read_json(path), character_id)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=CATALOG_DIR / "character_registry.json")
    parser.add_argument("--dialogue", type=Path, default=CATALOG_DIR / "dialogue_catalog.jsonl")
    parser.add_argument("--output-dir", type=Path, default=CATALOG_DIR / "profiles" / "deepseek")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-attempts", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.batch_size < 1 or args.workers < 1 or args.samples < 2:
        parser.error("batch-size/workers must be positive and samples must be at least 2")

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    registry = read_json(args.registry.resolve())
    dialogue_samples = load_dialogue_samples(args.dialogue.resolve(), args.samples)
    characters = [
        character
        for character in registry
        if int(character.get("speakable_line_count", 0)) > 0
    ]
    if args.limit:
        characters = characters[: args.limit]

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    pending: list[dict[str, Any]] = []
    cached: dict[str, dict[str, Any]] = {}
    for character in characters:
        character_id = str(character["character_id"])
        profile_path = output_dir / f"{character_id}.json"
        existing = None if args.force else load_cached_profile(profile_path, character_id)
        if existing is not None:
            cached[character_id] = existing
        else:
            pending.append(build_character_input(character, dialogue_samples))

    batches = list(batched(pending, args.batch_size))
    print(
        json.dumps(
            {
                "model": args.model,
                "characters_selected": len(characters),
                "cached_profiles": len(cached),
                "pending_profiles": len(pending),
                "api_batches": len(batches),
                "batch_size": args.batch_size,
                "workers": args.workers,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    completed = 0
    run_metadata: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_map = {
            executor.submit(
                request_profiles,
                api_key,
                args.api_url,
                args.model,
                batch,
                args.timeout,
                args.max_attempts,
            ): batch
            for batch in batches
        }
        for future in as_completed(future_map):
            batch = future_map[future]
            batch_ids = [str(item["character_id"]) for item in batch]
            try:
                profiles, metadata = future.result()
                for profile in profiles:
                    character_id = str(profile["character_id"])
                    write_json_atomic(output_dir / f"{character_id}.json", profile)
                    cached[character_id] = profile
                run_metadata.append(metadata)
                completed += len(profiles)
                print(
                    json.dumps(
                        {
                            "completed": completed,
                            "pending_total": len(pending),
                            "last_batch": batch_ids,
                            "elapsed_seconds": metadata["elapsed_seconds"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            except Exception as error:  # Keep other independent batches running.
                message = f"batch {batch_ids}: {error}"
                errors.append(message)
                print(json.dumps({"error": message}, ensure_ascii=False), flush=True)

    selected_ids = [str(character["character_id"]) for character in characters]
    combined = [cached[character_id] for character_id in selected_ids if character_id in cached]
    write_json_atomic(output_dir.parent / "character_profiles.json", combined)
    run_summary = {
        "model": args.model,
        "characters_selected": len(characters),
        "profiles_available": len(combined),
        "profiles_generated_this_run": completed,
        "errors": errors,
        "batches": run_metadata,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json_atomic(output_dir.parent / "profile_run.json", run_summary)
    print(json.dumps(run_summary, ensure_ascii=False), flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
