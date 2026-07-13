# Copyright (C) 2025 Advanced Micro Devices, Inc. All rights reserved.
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# Use a verified ROCm PyTorch image as base
ARG BASE_IMAGE=ghcr.io/amdresearch/auplc-base:latest
FROM ${BASE_IMAGE}

ARG LEROBOT_SMOLVLA_BASE_REVISION=c83c3163b8ca9b7e67c509fffd9121e66cb96205
ARG SMOLVLM2_500M_VIDEO_INSTRUCT_REVISION=7b375e1b73b11138ff12fe22c8f2822d8fe03467
ARG MT05_SMOLVLA_LIFT_REVISION=90bfedc7c7fc76d04967a50c9b2267517fc96615
ARG XLEROBOT_REVISION=51ca0ec31bdb48713b94bacdba828bf8d889296b

USER root
# Vulkan backend and GUI
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget git ca-certificates locales \
    ffmpeg \
    libgl1 libglx-mesa0 libgl1-mesa-dri libegl1 libgbm1 libglib2.0-0 \
    libvulkan1 mesa-vulkan-drivers vulkan-tools \
 && rm -rf /var/lib/apt/lists/*

RUN ln -sf /opt/rocm/lib/llvm/bin/ld.lld /usr/local/bin/ld.lld

# IMPORTANT: must remain under USER root here. pip install as jovyan silently
# falls back to ~/.local/, which gets masked by the PVC mount at runtime.

# Course packages not already in auplc-base (torch / numpy / jupyter ship there).
# The MuJoCo simulation core is pinned; the rest track compatible releases.
RUN pip3 install --no-cache-dir \
    "mujoco==3.9.0" \
    "gymnasium==1.3.0" \
    "robosuite==1.5.2" \
    robosuite_models \
    robot_descriptions \
    h5py \
    imageio \
    imageio-ffmpeg

# LeRobot (VLA policies) + the transformers version it requires (>=5.3.0,<6).
RUN pip3 install --no-cache-dir "lerobot[smolvla,pi]==0.5.0" \
 && pip3 install --no-cache-dir "transformers==5.3.0"

# XLeRobot MuJoCo assets, baked for offline use at runtime.
RUN git init /opt/xlerobot \
 && git -C /opt/xlerobot remote add origin https://github.com/Vector-Wangel/XLeRobot.git \
 && git -C /opt/xlerobot fetch --depth 1 origin "${XLEROBOT_REVISION}" \
 && git -C /opt/xlerobot checkout --detach "${XLEROBOT_REVISION}" \
 && rm -rf /opt/xlerobot/.git \
 && chmod -R a+rX /opt/xlerobot

# Bake the pinned Hugging Face snapshots MT05 needs into the native cache.
#   - lerobot/smolvla_base                        pretrained SmolVLA (cell 4)
#   - HuggingFaceTB/SmolVLM2-500M-Video-Instruct  its VLM backbone / tokenizer
#   - sonya-tw/mt05-smolvla-lift                  fully-trained checkpoint (appendix)
ENV HF_HOME=/opt/hf
RUN mkdir -p /opt/hf \
 && chown jovyan:1000 /opt/hf
USER jovyan

# Keep only the policy files that LeRobot loads, while preserving the Hub's
# blob/snapshot symlinks and refs/main alias for named offline loading.
RUN python3 -c "from huggingface_hub import snapshot_download; from pathlib import Path; repo_id='lerobot/smolvla_base'; revision='${LEROBOT_SMOLVLA_BASE_REVISION}'; snapshot_download(repo_id=repo_id, revision=revision, cache_dir='/opt/hf/hub', allow_patterns=['config.json', 'model.safetensors', 'policy_preprocessor.json', 'policy_preprocessor_step_5_normalizer_processor.safetensors', 'policy_postprocessor.json', 'policy_postprocessor_step_0_unnormalizer_processor.safetensors']); ref=Path('/opt/hf/hub') / ('models--' + repo_id.replace('/', '--')) / 'refs' / 'main'; ref.parent.mkdir(parents=True, exist_ok=True); ref.write_text(revision)"

# Keep the Transformers configuration, processor, and tokenizer metadata but
# intentionally omit the repository's ONNX variants.
RUN python3 -c "from huggingface_hub import snapshot_download; from pathlib import Path; repo_id='HuggingFaceTB/SmolVLM2-500M-Video-Instruct'; revision='${SMOLVLM2_500M_VIDEO_INSTRUCT_REVISION}'; snapshot_download(repo_id=repo_id, revision=revision, cache_dir='/opt/hf/hub', allow_patterns=['model.safetensors', 'config.json', 'generation_config.json', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json', 'added_tokens.json', 'chat_template.json', 'chat_template.jinja', 'processor_config.json', 'preprocessor_config.json', 'video_preprocessor_config.json', 'vocab.json', 'merges.txt', 'tokenizer.model'], ignore_patterns=['onnx/**', '*.onnx']); ref=Path('/opt/hf/hub') / ('models--' + repo_id.replace('/', '--')) / 'refs' / 'main'; ref.parent.mkdir(parents=True, exist_ok=True); ref.write_text(revision)"

RUN python3 -c "from huggingface_hub import snapshot_download; from pathlib import Path; repo_id='sonya-tw/mt05-smolvla-lift'; revision='${MT05_SMOLVLA_LIFT_REVISION}'; snapshot_download(repo_id=repo_id, revision=revision, cache_dir='/opt/hf/hub', allow_patterns=['config.json', 'model.safetensors', 'policy_preprocessor.json', 'policy_preprocessor_step_5_normalizer_processor.safetensors', 'policy_postprocessor.json', 'policy_postprocessor_step_0_unnormalizer_processor.safetensors']); ref=Path('/opt/hf/hub') / ('models--' + repo_id.replace('/', '--')) / 'refs' / 'main'; ref.parent.mkdir(parents=True, exist_ok=True); ref.write_text(revision)"

USER root
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# Copy related rocm notebooks into the docker.
RUN mkdir -p /opt/workspace/MuJoCo-Torch
COPY ./course_data /opt/workspace/MuJoCo-Torch

USER root
RUN chown -R jovyan:1000 /opt/workspace
USER jovyan
