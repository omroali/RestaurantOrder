#!/bin/bash
#
# download_models.sh  –  Pre-download faster-whisper models for offline use.
#
# Usage:
#   ./download_models.sh              # downloads 'small' + 'base' to default dir
#   ./download_models.sh tiny base    # download specific models
#   ./download_models.sh small /path/to/cache  # custom cache directory
#
# Default cache dir:  ./models/huggingface  (relative to this script's location)
# This is the same path mounted as a volume in run_robot_docker.sh.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CACHE_DIR="${SCRIPT_DIR}/huggingface"

MODELS=("${@}")

# If no models specified, use sensible defaults
if [ ${#MODELS[@]} -eq 0 ] || [ -d "${MODELS[0]}" ] 2>/dev/null; then
    # First arg might be a cache dir instead of a model
    if [ -d "${MODELS[0]}" ] 2>/dev/null; then
        CACHE_DIR="$(cd "${MODELS[0]}" && pwd)"
        MODELS=("${MODELS[@]:1}")
    fi
    # Still no models? Use defaults
    if [ ${#MODELS[@]} -eq 0 ]; then
        MODELS=("small" "base")
    fi
fi

mkdir -p "${CACHE_DIR}"

echo "============================================"
echo "  Downloading faster-whisper models"
echo "  Models:    ${MODELS[*]}"
echo "  Cache dir: ${CACHE_DIR}"
echo "============================================"

for model in "${MODELS[@]}"; do
    echo ""
    echo "--- Downloading '${model}' model ---"
    python3 -c "
import os
os.environ['HF_HUB_CACHE'] = '${CACHE_DIR}'
from faster_whisper import WhisperModel
print(f'Downloading {model} to {os.environ[\"HF_HUB_CACHE\"]} …')
model = WhisperModel('${model}', device='cpu', compute_type='int8', download_root='${CACHE_DIR}')
print('Done.')
"
done

echo ""
echo "============================================"
echo "  All models downloaded."
echo "  Cache contents:"
ls -lh "${CACHE_DIR}"
echo "============================================"
