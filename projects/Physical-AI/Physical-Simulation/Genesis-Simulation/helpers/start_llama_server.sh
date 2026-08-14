#!/usr/bin/env bash
# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

set -euo pipefail

LLAMA_SERVER_BIN="${LLAMA_SERVER_BIN:-/opt/llama/bin/llama-server}"
LLAMA_MODEL_PATH="${LLAMA_MODEL_PATH:-/opt/workspace/PhySim/models/Llama-3.2-3B-Instruct-Q4_K_M.gguf}"
LLAMA_HOST="${LLAMA_HOST:-127.0.0.1}"
LLAMA_PORT="${LLAMA_PORT:-8081}"
LLAMA_GPU_LAYERS="${LLAMA_GPU_LAYERS:-99}"
LLAMA_CTX_SIZE="${LLAMA_CTX_SIZE:-2048}"
LLAMA_PARALLEL="${LLAMA_PARALLEL:-1}"

if [[ ! -x "$LLAMA_SERVER_BIN" ]]; then
  echo "ERROR: llama-server is not executable: $LLAMA_SERVER_BIN" >&2
  echo "Set LLAMA_SERVER_BIN to a llama.cpp Vulkan or ROCm/HIP build." >&2
  exit 1
fi

if [[ ! -f "$LLAMA_MODEL_PATH" ]]; then
  echo "ERROR: GGUF model was not found: $LLAMA_MODEL_PATH" >&2
  echo "Set LLAMA_MODEL_PATH to Llama-3.2-3B-Instruct-Q4_K_M.gguf." >&2
  exit 1
fi

if python3 -c \
  "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://${LLAMA_HOST}:${LLAMA_PORT}/health', timeout=2).status == 200 else 1)" \
  >/dev/null 2>&1; then
  echo "llama-server is already healthy at http://${LLAMA_HOST}:${LLAMA_PORT}"
  exit 0
fi

if python3 -c \
  "import socket, sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('${LLAMA_HOST}', ${LLAMA_PORT})) == 0 else 1)" \
  >/dev/null 2>&1; then
  echo "ERROR: ${LLAMA_HOST}:${LLAMA_PORT} is already occupied by another process." >&2
  exit 1
fi

echo "Starting llama-server"
echo "  binary : $LLAMA_SERVER_BIN"
echo "  model  : $LLAMA_MODEL_PATH"
echo "  URL    : http://${LLAMA_HOST}:${LLAMA_PORT}"
echo "Keep this terminal open. Press Ctrl+C to stop the server."

exec "$LLAMA_SERVER_BIN" \
  --model "$LLAMA_MODEL_PATH" \
  --host "$LLAMA_HOST" \
  --port "$LLAMA_PORT" \
  --n-gpu-layers "$LLAMA_GPU_LAYERS" \
  --ctx-size "$LLAMA_CTX_SIZE" \
  --parallel "$LLAMA_PARALLEL" \
  --no-warmup
