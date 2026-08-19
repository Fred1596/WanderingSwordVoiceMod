#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="${PID_FILE:-$PROJECT_ROOT/logs/server_pipeline.pid}"
POLL_SECONDS="${POLL_SECONDS:-60}"

cd "$PROJECT_ROOT"

if [[ ! -f "$PID_FILE" ]]; then
  echo "Pipeline PID file not found: $PID_FILE" >&2
  exit 1
fi

pipeline_pid="$(tr -d '[:space:]' < "$PID_FILE")"
if [[ ! "$pipeline_pid" =~ ^[0-9]+$ ]]; then
  echo "Invalid pipeline PID: $pipeline_pid" >&2
  exit 1
fi

echo "Waiting for pipeline PID $pipeline_pid..."
while kill -0 "$pipeline_pid" 2>/dev/null; do
  sleep "$POLL_SECONDS"
done

echo "Pipeline process ended; verifying and packaging the generated delta..."
bash package_server_delta.sh
