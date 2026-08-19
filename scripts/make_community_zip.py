#!/usr/bin/env python3
"""Create and verify a Zip64 community release archive."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = PROJECT_ROOT / "community_release"
VERSION = (
    PROJECT_ROOT / "community_source" / "VERSION.txt"
).read_text(encoding="utf-8").strip()
SOURCE = RELEASE_ROOT / f"WanderingSwordVoiceMod-v{VERSION}"
ARCHIVE = RELEASE_ROOT / f"WanderingSwordVoiceMod-v{VERSION}.zip"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def main() -> int:
    if not SOURCE.is_dir():
        raise FileNotFoundError(SOURCE)
    if ARCHIVE.exists():
        raise FileExistsError(ARCHIVE)
    files = sorted(path for path in SOURCE.rglob("*") if path.is_file())
    with zipfile.ZipFile(
        ARCHIVE,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as archive:
        for index, path in enumerate(files, start=1):
            archive_name = (Path(SOURCE.name) / path.relative_to(SOURCE)).as_posix()
            archive.write(path, archive_name)
            if index % 1000 == 0:
                print(f"Archived: {index}/{len(files)}", flush=True)

    with zipfile.ZipFile(ARCHIVE, "r", allowZip64=True) as archive:
        bad_file = archive.testzip()
        if bad_file is not None:
            raise RuntimeError(f"CRC verification failed: {bad_file}")
        entry_count = len(archive.infolist())

    digest = sha256_file(ARCHIVE)
    checksum_path = ARCHIVE.with_suffix(ARCHIVE.suffix + ".sha256.txt")
    checksum_path.write_text(
        f"{digest}  {ARCHIVE.name}\n",
        encoding="ascii",
    )
    print(f"Archive: {ARCHIVE}")
    print(f"Bytes: {ARCHIVE.stat().st_size}")
    print(f"Entries: {entry_count}")
    print(f"SHA256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
