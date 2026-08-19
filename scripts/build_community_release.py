#!/usr/bin/env python3
"""Build the public, model-free Wandering Sword voice mod release directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "community_source"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "community_release"
VERSION = (SOURCE_ROOT / "VERSION.txt").read_text(encoding="utf-8").strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def copy_required(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def resolve_game_executable(requested: Path | None) -> Path:
    candidates: list[Path] = []
    if requested is not None:
        candidates.append(requested.expanduser())
    environment_root = os.environ.get("WS_GAME_ROOT")
    if environment_root:
        candidates.append(
            Path(environment_root)
            / "Wandering_Sword"
            / "Binaries"
            / "Win64"
            / "JH-Win64-Shipping.exe"
        )
    for root in (PROJECT_ROOT.parent, PROJECT_ROOT.parent.parent):
        candidates.append(
            root
            / "Wandering_Sword"
            / "Binaries"
            / "Win64"
            / "JH-Win64-Shipping.exe"
        )
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
    raise FileNotFoundError(
        "Game executable was not found. Pass --game-executable with the full "
        "path to JH-Win64-Shipping.exe, or set WS_GAME_ROOT."
    )


def configure_ue4ss_engine_version(settings_path: Path) -> None:
    text = settings_path.read_text(encoding="utf-8")
    section_match = re.search(
        r"(?ms)^\s*\[EngineVersionOverride\]\s*\n(?P<body>.*?)(?=^\s*\[|\Z)",
        text,
    )
    if section_match is None:
        raise RuntimeError(f"EngineVersionOverride section is missing: {settings_path}")
    body = section_match.group("body")
    body, major_count = re.subn(
        r"(?m)^([ \t]*MajorVersion[ \t]*=[ \t]*)[^;\r\n]*",
        r"\g<1>4",
        body,
        count=1,
    )
    body, minor_count = re.subn(
        r"(?m)^([ \t]*MinorVersion[ \t]*=[ \t]*)[^;\r\n]*",
        r"\g<1>26",
        body,
        count=1,
    )
    if major_count != 1 or minor_count != 1:
        raise RuntimeError(f"Engine version keys are missing: {settings_path}")
    configured = text[: section_match.start("body")] + body + text[section_match.end("body") :]
    settings_path.write_text(configured, encoding="utf-8", newline="")
    if not re.search(r"(?m)^[ \t]*MajorVersion[ \t]*=[ \t]*4[ \t]*$", configured):
        raise RuntimeError("Failed to set UE4SS MajorVersion to 4")
    if not re.search(r"(?m)^[ \t]*MinorVersion[ \t]*=[ \t]*26[ \t]*$", configured):
        raise RuntimeError("Failed to set UE4SS MinorVersion to 26")


def build_compact_lookup(destination: Path) -> dict[str, int]:
    source = PROJECT_ROOT / "offline" / "manifest" / "runtime_lookup.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    exact = {
        key: value["audio_file"]
        for key, value in document["exact"].items()
    }
    text_fallback = {
        key: value["audio_file"]
        for key, value in document.get("text_fallback", {}).items()
    }
    compact = {
        "version": 2,
        "algorithm": document.get("algorithm", "sha256(normalized_speaker\\0normalized_text)"),
        "exact": exact,
        "text_fallback": text_fallback,
        "prefix": document.get("prefix", []),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(compact, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return {
        "exact": len(exact),
        "text_fallback": len(text_fallback),
        "prefix": len(compact["prefix"]),
    }


def load_expected_audio() -> tuple[list[Path], dict[str, int]]:
    jobs_path = PROJECT_ROOT / "offline" / "manifest" / "dialogue_jobs.jsonl"
    stats_path = PROJECT_ROOT / "offline" / "manifest" / "offline_stats.json"
    expected: set[Path] = set()
    with jobs_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            expected.add(PROJECT_ROOT / record["audio_file"])
    missing = sorted(path for path in expected if not path.is_file())
    if missing:
        raise RuntimeError(
            f"Missing {len(missing)} generated dialogue WAVs; first: {missing[0]}"
        )
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    return sorted(expected), stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--game-executable",
        type=Path,
        help="Full path to JH-Win64-Shipping.exe used for the compatibility hash.",
    )
    args = parser.parse_args()

    output_root = args.output_root.resolve()
    release = output_root / f"WanderingSwordVoiceMod-v{VERSION}"
    if release.exists():
        raise FileExistsError(
            f"Release directory already exists; move or remove it first: {release}"
        )
    output_root.mkdir(parents=True, exist_ok=True)

    shutil.copytree(SOURCE_ROOT, release)
    for publisher_only_name in ("发布前检查清单.md", "社区发布文案模板.md"):
        publisher_only_path = release / publisher_only_name
        if publisher_only_path.is_file():
            publisher_only_path.unlink()
    tools_directory = release / "tools"
    if tools_directory.is_dir():
        shutil.rmtree(tools_directory)

    lookup_stats = build_compact_lookup(
        release / "data" / "runtime_lookup.compact.json"
    )

    source_audio = PROJECT_ROOT / "offline" / "audio"
    destination_audio = release / "data" / "offline" / "audio"
    wav_files, offline_stats = load_expected_audio()
    audio_bytes = 0
    for index, source in enumerate(wav_files, start=1):
        relative = source.relative_to(source_audio)
        link_or_copy(source, destination_audio / relative)
        audio_bytes += source.stat().st_size
        if index % 1000 == 0:
            print(f"Linked audio: {index}/{len(wav_files)}", flush=True)

    runtime_source = PROJECT_ROOT / "vendor" / "ue4ss-runtime-extracted"
    copy_required(
        runtime_source / "dwmapi.dll",
        release / "payload" / "ue4ss" / "dwmapi.dll",
    )
    for filename in ("UE4SS.dll", "UE4SS-settings.ini", "LICENSE"):
        copy_required(
            runtime_source / "ue4ss" / filename,
            release / "payload" / "ue4ss" / "ue4ss" / filename,
        )
    configure_ue4ss_engine_version(
        release / "payload" / "ue4ss" / "ue4ss" / "UE4SS-settings.ini"
    )
    copy_required(
        runtime_source / "ue4ss" / "LICENSE",
        release / "licenses" / "UE4SS-LICENSE.txt",
    )
    copy_required(
        PROJECT_ROOT
        / "src"
        / "ue4ss"
        / "Mods"
        / "WanderingSwordVoiceProbe"
        / "Scripts"
        / "main.lua",
        release
        / "payload"
        / "WanderingSwordVoiceProbe"
        / "Scripts"
        / "main.lua",
    )

    game_executable = resolve_game_executable(args.game_executable)
    manifest = {
        "name": "Wandering Sword Offline AI Voice Mod",
        "version": VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "game": {
            "steam_app_id": 1876890,
            "engine": "UE4.26",
            "tested_executable_sha256": sha256_file(game_executable),
        },
        "content": {
            "voice_groups": offline_stats["voice_groups"],
            "dialogue_jobs": offline_stats["dialogue_jobs"],
            "unique_dialogue_wavs": len(wav_files),
            "tts_characters": offline_stats["tts_characters"],
            "audio_bytes": audio_bytes,
            "exact_lookup_entries": lookup_stats["exact"],
            "text_fallback_entries": lookup_stats["text_fallback"],
            "prefix_lookup_entries": lookup_stats["prefix"],
        },
        "runtime": {
            "ue4ss_version": "v3.0.1-1018-g662df915",
            "engine_version_override": "4.26",
            "playback_speed_default": 1.0,
            "playback_speed_minimum": 1.0,
            "playback_speed_maximum": 1.5,
            "pitch_preserving_time_stretch": True,
            "requires_python": False,
            "requires_cuda": False,
            "requires_network": False,
        },
    }
    (release / "package_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    print(f"Release ready: {release}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
