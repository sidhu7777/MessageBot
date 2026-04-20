#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/root/MessageBot"
VENV_PY="$ROOT_DIR/.venv310/bin/python"
APP="tests.live_whisper_browser_stream:app"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8010}"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing Python venv at $VENV_PY" >&2
  exit 1
fi

# Ensure NVIDIA device nodes exist for CUDA/NVML access.
nvidia-modprobe -u -c=0 || true
[ -e /dev/nvidiactl ] || mknod -m 666 /dev/nvidiactl c 195 255 || true
[ -e /dev/nvidia0 ] || mknod -m 666 /dev/nvidia0 c 195 0 || true
[ -e /dev/nvidia-modeset ] || mknod -m 666 /dev/nvidia-modeset c 195 254 || true
[ -e /dev/nvidia-uvm ] || mknod -m 666 /dev/nvidia-uvm c 235 0 || true
[ -e /dev/nvidia-uvm-tools ] || mknod -m 666 /dev/nvidia-uvm-tools c 235 1 || true

export PYTHONPATH="$ROOT_DIR"
export WHISPER_DEVICE="${WHISPER_DEVICE:-cuda}"
export WHISPER_COMPUTE_TYPE="${WHISPER_COMPUTE_TYPE:-float16}"
export OLLAMA_NUM_GPU="${OLLAMA_NUM_GPU:-1}"

# Expose pip-installed NVIDIA runtime libs (both cu12/cu13 packages if present).
if [[ -d "$ROOT_DIR/.venv310/lib/python3.10/site-packages/nvidia" ]]; then
  while IFS= read -r libdir; do
    export LD_LIBRARY_PATH="${libdir}:${LD_LIBRARY_PATH:-}"
  done < <(find "$ROOT_DIR/.venv310/lib/python3.10/site-packages/nvidia" -type d -name lib | sort -r)
fi

echo "GPU probe:"
nvidia-smi | sed -n '1,12p' || true

exec "$VENV_PY" -m uvicorn "$APP" --host "$HOST" --port "$PORT"
