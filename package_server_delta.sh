#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILE_LIST="$PROJECT_ROOT/offline/manifest/server_delta_files.txt"
OUTPUT="${1:-$PROJECT_ROOT/dist/wsvoice_generated_delta.tar.gz}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CLEAN_FILE_LIST="$(mktemp)"
trap 'rm -f "$CLEAN_FILE_LIST"' EXIT

cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/verify_generation_ready.py --require-complete

if [[ ! -f "$FILE_LIST" ]]; then
    echo "Missing delta file list: $FILE_LIST" >&2
    exit 2
fi

# The manifest is built on Windows and may contain CRLF line endings. A raw
# Linux `read`/`tar -T` treats the trailing carriage return as part of the
# filename, so normalize a temporary copy before validating and packaging.
sed 's/\r$//' "$FILE_LIST" > "$CLEAN_FILE_LIST"
while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    if [[ ! -f "$path" ]]; then
        echo "Missing generated delta file: $path" >&2
        exit 3
    fi
done < "$CLEAN_FILE_LIST"

mkdir -p "$(dirname "$OUTPUT")"
if command -v pigz >/dev/null 2>&1; then
    tar -I 'pigz -1' -cf "$OUTPUT" -T "$CLEAN_FILE_LIST"
else
    tar -czf "$OUTPUT" -T "$CLEAN_FILE_LIST"
fi
sha256sum "$OUTPUT" | tee "$OUTPUT.sha256.txt"
du -h "$OUTPUT"
echo "Delta package ready: $OUTPUT"
