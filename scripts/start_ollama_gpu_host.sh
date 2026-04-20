#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-11434}"
OLLAMA_BIN="${OLLAMA_BIN:-/usr/local/bin/ollama}"
OLLAMA_MODELS="${OLLAMA_MODELS:-/usr/share/ollama/.ollama/models}"

if [[ ! -x "$OLLAMA_BIN" ]]; then
  echo "Missing ollama binary at $OLLAMA_BIN" >&2
  exit 1
fi

nvidia-modprobe -u -c=0 || true

export OLLAMA_HOST="http://${HOST}:${PORT}"
export OLLAMA_MODELS
export OLLAMA_LLM_LIBRARY="${OLLAMA_LLM_LIBRARY:-cuda_v13}"
export LD_LIBRARY_PATH="/usr/local/lib/ollama:/usr/local/lib/ollama/cuda_v13:/usr/local/lib/ollama/mlx_cuda_v13:/usr/local/lib:${LD_LIBRARY_PATH:-}"

echo "GPU probe:"
nvidia-smi | sed -n '1,12p' || true
echo "Starting Ollama with OLLAMA_HOST=${OLLAMA_HOST}"

exec "$OLLAMA_BIN" serve
