ARG BASE_IMAGE=ghcr.io/amdresearch/auplc-base:latest

ARG LEROBOT_REF=95211b98

FROM ${BASE_IMAGE}
ARG LEROBOT_REF

USER root

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg python-is-python3 libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --timeout 500 \
        "lerobot[feetech,dataset,smolvla] @ git+https://github.com/huggingface/lerobot.git@${LEROBOT_REF}" \
        'transformers>=5,<6' 'accelerate>=1.0'

RUN pip uninstall -y opencv-python-headless opencv-python && \
    pip install --no-cache-dir 'opencv-python>=4.13,<4.14'

RUN python3 -c "import torch, cv2; \
assert 'rocm' in torch.__version__, f'expected a ROCm torch, got {torch.__version__}'; \
gui=next((l.split(':',1)[1].strip() for l in cv2.getBuildInformation().splitlines() if l.strip().startswith('GUI:')), ''); \
assert gui and gui != 'NONE', f'opencv has no GUI support (GUI: {gui})'; \
print('ok:', torch.__version__, '| cv2 GUI:', gui)"

RUN mkdir -p /home/jovyan/lerobot_data /home/jovyan/lerobot_out && \
    chown -R jovyan:1000 /home/jovyan/lerobot_data /home/jovyan/lerobot_out

ENV PYTHONPATH=/workspace/lerobot/src
ENV TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1

USER jovyan
WORKDIR /workspace/lab
