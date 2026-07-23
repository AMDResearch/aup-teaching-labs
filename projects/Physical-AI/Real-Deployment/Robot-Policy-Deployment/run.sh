#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

docker run -it --rm \
    --privileged \
    --group-add dialout \
    --group-add video \
    --shm-size=8g \
    -p 8888:8888 \
    -v "$SCRIPT_DIR":/opt/workspace/lerobot \
    --entrypoint jupyter \
    lerobot-notebook lab --ip=0.0.0.0 --port=8888 --no-browser
