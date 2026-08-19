#!/usr/bin/env python3
"""Reject secrets, binary payloads, model weights, and unexpected large files."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 10 * 1024 * 1024
LARGE_FILE_ALLOWLIST = {Path("catalog/dialogue_catalog.jsonl")}
SKIP_FILES = {
    Path(".gitignore"),
    Path("scripts/check_repository_hygiene.py"),
}
FORBIDDEN_SUFFIXES = {
    ".7z",
    ".ckpt",
    ".dll",
    ".exe",
    ".flac",
    ".locres",
    ".mp3",
    ".ogg",
    ".onnx",
    ".pak",
    ".pth",
    ".pt",
    ".rar",
    ".safetensors",
    ".tar",
    ".tgz",
    ".uasset",
    ".wav",
    ".zip",
}
TEXT_SUFFIXES = {
    "",
    ".cmd",
    ".example",
    ".ini",
    ".json",
    ".lua",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".txt",
    ".yaml",
    ".yml",
}
SUSPICIOUS_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "API token": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "Windows user path": re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+", re.I),
    "project-specific conda path": re.compile(r"[A-Za-z]:\\Anaconda\\envs\\", re.I),
    "rental-server path": re.compile(r"/root/(?:autodl|autodl-tmp)(?:/|\b)"),
    "rental-server endpoint": re.compile(r"(?:seetacloud|connect\.west[ac-z]?\.)", re.I),
}


def iter_files() -> list[Path]:
    """Inspect commit candidates, not ignored local models or generated audio."""

    if (ROOT / ".git").exists():
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
        )
        relative_paths = [
            Path(value.decode("utf-8"))
            for value in result.stdout.split(b"\0")
            if value
        ]
        return sorted(ROOT / path for path in relative_paths)

    ignored_roots = {
        "cache",
        "community_release",
        "dist",
        "extracted",
        "logs",
        "models",
        "offline",
        "outputs",
        "previews",
        "vendor",
    }
    output: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(ROOT).parts
        if not parts or parts[0] in ignored_roots:
            continue
        if ".git" not in parts:
            output.append(path)
    return sorted(output)


def main() -> int:
    errors: list[str] = []
    files = iter_files()
    for path in files:
        relative = path.relative_to(ROOT)
        size = path.stat().st_size
        if size > MAX_FILE_BYTES and relative not in LARGE_FILE_ALLOWLIST:
            errors.append(f"large file ({size} bytes): {relative}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"forbidden payload type: {relative}")
        if relative in SKIP_FILES or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"unexpected binary file: {relative}")
            continue
        for label, pattern in SUSPICIOUS_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{label}: {relative}")

    if errors:
        print("Repository hygiene check failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"Repository hygiene check passed: {len(files)} files checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
