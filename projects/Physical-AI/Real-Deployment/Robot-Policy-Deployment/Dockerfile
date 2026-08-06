ARG BASE_IMAGE=ghcr.io/amdresearch/auplc-base:latest
FROM ${BASE_IMAGE}

USER root

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg cmake build-essential \
        libegl1-mesa-dev libgl1-mesa-dev libgles2-mesa-dev \
        speech-dispatcher && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --timeout 300 'lerobot[feetech]>=0.5.0' 'transformers>=5,<6' 'accelerate>=1.0' num2words

RUN mkdir -p /opt/workspace/lerobot && \
    chown -R jovyan:1000 /opt/workspace/lerobot

USER jovyan
WORKDIR /opt/workspace/lerobot
