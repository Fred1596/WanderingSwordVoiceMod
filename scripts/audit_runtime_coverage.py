#!/usr/bin/env python3
"""Audit captured game dialogue against the generated offline runtime index."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOOKUP = PROJECT_ROOT / "offline" / "manifest" / "runtime_lookup.json"
SYSTEM_NARRATION_TEXTS = {
    "即将进入剧情战斗，建议少侠及时调整战斗模式。",
}


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc


def normalize(value: str, form: str = "NFC") -> str:
    value = unicodedata.normalize(form, str(value or ""))
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def lookup_key(speaker: str, text: str) -> str:
    return hashlib.sha256(f"{speaker}\0{text}".encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event_file", type=Path, help="dialogue_events.jsonl")
    parser.add_argument("--lookup", type=Path, default=DEFAULT_LOOKUP)
    parser.add_argument(
        "--no-current-bridge-rules",
        action="store_true",
        help="Do not remap known system narration captured by an older bridge.",
    )
    parser.add_argument("--show-misses", type=int, default=30)
    args = parser.parse_args()

    lookup = json.loads(args.lookup.read_text(encoding="utf-8"))
    exact = lookup["exact"]
    text_fallback = lookup.get("text_fallback") or {}
    prefixes = lookup.get("prefix") or []
    counts: Counter[str] = Counter()
    misses: Counter[tuple[str, str]] = Counter()

    for event in iter_jsonl(args.event_file):
        if event.get("event") != "dialogue":
            continue
        speaker = str(event.get("speaker") or "旁白")
        text = str(event.get("text") or "")
        if not text.strip():
            continue
        counts["dialogue_events"] += 1
        if not args.no_current_bridge_rules and text in SYSTEM_NARRATION_TEXTS:
            speaker = "旁白"
            counts["current_bridge_narrator_remaps"] += 1

        matched: dict[str, Any] | None = None
        for mode, form in (("nfc", "NFC"), ("nfkc", "NFKC")):
            normalized_speaker = normalize(speaker, form)
            normalized_text = normalize(text, form)
            matched = exact.get(lookup_key(normalized_speaker, normalized_text))
            if matched:
                counts[f"matched_{mode}"] += 1
                break
        if matched is None:
            for mode, form in (("nfc", "NFC"), ("nfkc", "NFKC")):
                normalized_text = normalize(text, form)
                text_key = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
                matched = text_fallback.get(text_key)
                if matched:
                    counts[f"matched_text_{mode}"] += 1
                    break
        if matched is None:
            normalized_speaker = normalize(speaker, "NFKC")
            normalized_text = normalize(text, "NFKC")
            for candidate in prefixes:
                if normalize(candidate.get("speaker", ""), "NFKC") != normalized_speaker:
                    continue
                if normalized_text.startswith(
                    normalize(candidate.get("text_prefix", ""), "NFKC")
                ):
                    matched = candidate
                    counts["matched_prefix"] += 1
                    break

        if matched is None:
            counts["missing"] += 1
            misses[(speaker, text)] += 1
            continue
        audio_path = PROJECT_ROOT / matched["audio_file"]
        if audio_path.is_file():
            counts["audio_ready"] += 1
        else:
            counts["audio_pending"] += 1

    matched_total = sum(
        counts[key]
        for key in (
            "matched_nfc",
            "matched_nfkc",
            "matched_text_nfc",
            "matched_text_nfkc",
            "matched_prefix",
        )
    )
    total = counts["dialogue_events"]
    summary = {
        "event_file": str(args.event_file.resolve()),
        "dialogue_events": total,
        "indexed": matched_total,
        "index_coverage_percent": round(100 * matched_total / total, 3) if total else 0,
        "missing": counts["missing"],
        "match_modes": {
            "nfc": counts["matched_nfc"],
            "nfkc": counts["matched_nfkc"],
            "text_nfc": counts["matched_text_nfc"],
            "text_nfkc": counts["matched_text_nfkc"],
            "prefix": counts["matched_prefix"],
        },
        "current_bridge_narrator_remaps": counts["current_bridge_narrator_remaps"],
        "audio_ready_now": counts["audio_ready"],
        "audio_pending_server_generation": counts["audio_pending"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if misses and args.show_misses:
        print("\nTop missing dialogue:")
        for (speaker, text), count in misses.most_common(args.show_misses):
            print(f"[MISS x{count}] {speaker}: {text}")
    return 1 if counts["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
