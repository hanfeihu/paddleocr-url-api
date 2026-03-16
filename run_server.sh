#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
  echo "Missing .venv. Create it first: /opt/homebrew/bin/python3.11 -m venv .venv" 1>&2
  exit 1
fi

source ".venv/bin/activate"

# If the offline bundle sets PADDLE_PDX_CACHE_HOME, keep models in that path.
export PADDLE_PDX_CACHE_HOME="${PADDLE_PDX_CACHE_HOME:-}"

export OCR_WORKERS="${OCR_WORKERS:-6}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export OCR_DOWNLOAD_CONCURRENCY="${OCR_DOWNLOAD_CONCURRENCY:-16}"
export OCR_MAX_URLS="${OCR_MAX_URLS:-50}"
export OCR_SIZE_GATE="${OCR_SIZE_GATE:-1200}"
export OCR_MAX_BYTES="${OCR_MAX_BYTES:-15728640}"
export OCR_CONNECT_TIMEOUT="${OCR_CONNECT_TIMEOUT:-5}"
export OCR_READ_TIMEOUT="${OCR_READ_TIMEOUT:-15}"

export OCR_PORT="${OCR_PORT:-8000}"

# Avoid slow "model hoster connectivity" checks at startup.
export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="${PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK:-True}"

exec uvicorn app:app --host 0.0.0.0 --port "$OCR_PORT"
