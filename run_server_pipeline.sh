#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ANCHOR_BATCH="${ANCHOR_BATCH:-8}"
DIALOGUE_BATCH="${DIALOGUE_BATCH:-20}"

export HF_HOME="${HF_HOME:-$PROJECT_ROOT/cache/huggingface}"
export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$PROJECT_ROOT/cache/modelscope}"
export TOKENIZERS_PARALLELISM=false

cd "$PROJECT_ROOT"
mkdir -p logs

RUN_LOG="${RUN_LOG:-logs/server_pipeline_$(date -u +%Y%m%d_%H%M%S).log}"
exec > >(tee -a "$RUN_LOG") 2>&1

echo "Run log: $RUN_LOG"

"$PYTHON_BIN" bridge/check_server_requirements.py --require-gpu
"$PYTHON_BIN" scripts/verify_generation_ready.py
"$PYTHON_BIN" bridge/design_offline_anchors.py --batch-size "$ANCHOR_BATCH"
"$PYTHON_BIN" bridge/synthesize_offline_dialogue.py --batch-size "$DIALOGUE_BATCH"
"$PYTHON_BIN" scripts/verify_generation_ready.py --require-complete

echo "All offline anchors and dialogue audio have been generated."
