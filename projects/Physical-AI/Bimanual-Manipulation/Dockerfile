# Bimanual-Manipulation lab: one environment for recording, training and evaluation.
#
# The image carries no lab code and no lerobot source -- only the dependencies. Two directories
# are mounted in at run time, because they are two separate repositories:
#
#   this lab directory  -> /workspace/lab        (inside aup-teaching-labs)
#   the lerobot clone   -> /workspace/lerobot    (its own checkout, driver and patches applied)
#
# PYTHONPATH puts the mounted lerobot source ahead of the pip-installed copy, so the patched code
# is what runs and editing either repository on the host takes effect without a rebuild.
#
# Setup, once:
#   git clone https://github.com/amd-weiliao/aup-teaching-labs.git ~/aup-teaching-labs
#   cd ~/aup-teaching-labs/projects/Physical-AI/Bimanual-Manipulation
#   ./setup_lerobot.sh                  # clones lerobot at LEROBOT_REF, adds the driver, patches
#   docker build -t auplc-bimanual .
#
# Then everything goes through ./run.sh, which assembles the GPU, serial, camera and X11 flags --
# there are enough of them that getting one wrong is the normal outcome of typing it by hand:
#   ./run.sh --check                    # verify the environment first
#   ./run.sh ./record_phases.sh blue open,close
#   ./run.sh ./train_pipeline.sh
#   ./run.sh python chain_eval.py --stages ... --colour blue
#   ./run.sh                            # interactive shell
#
# NO torch is installed here. The base image ships one built against AMD's rocm-sdk wheels
# (2.9.1+rocm7.13.0 as of writing) and lerobot's declared range accepts it, so pip leaves it
# alone. The build check below fails the build if that ever stops being true and an upstream CUDA
# wheel replaces it.

ARG BASE_IMAGE=ghcr.io/amdresearch/auplc-base:latest
# Must match the commit the mounted checkout is on. See the setup block above.
ARG LEROBOT_REF=95211b98

FROM ${BASE_IMAGE}
ARG LEROBOT_REF

USER root

# python-is-python3: the base ships python3 only, and the scripts and docs here say `python`.
# libgl1/libglib2.0-0: the GUI opencv build below links against them.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg python-is-python3 libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

# Installed for its dependencies and console scripts; the source itself is shadowed by the mount.
# `dataset` is not optional despite the name: lerobot/rollout/__init__.py calls
# require_package("datasets").
#
# Installed from the SAME COMMIT the checkout is on, not from PyPI, because the two disagree
# about dependencies even at identical version numbers. lerobot 0.6.1 on PyPI asks for
# draccus>=0.11.6; commit 95211b98 pins draccus==0.10.0. Take the PyPI pins and draccus 0.11.6
# runs 0.10.0-era code: training completes normally and produces byte-identical losses, but the
# config.json it writes has no top-level "type" field, so every checkpoint it makes is
# unloadable. verify_env.py now compares the source's pins against what is installed.
RUN pip install --no-cache-dir --timeout 300 \
        "lerobot[feetech,dataset,smolvla] @ git+https://github.com/huggingface/lerobot.git@${LEROBOT_REF}" \
        'transformers>=5,<6' 'accelerate>=1.0'

# lerobot depends on opencv-python-headless, which is built without GUI support: cv2.imshow,
# which record_teleop.py, view_cams.py and chain_eval.py all call, raises "The function is not
# implemented" against it. Both packages unpack into the same cv2/ directory, so the headless one
# has to go first.
RUN pip uninstall -y opencv-python-headless opencv-python && \
    pip install --no-cache-dir 'opencv-python>=4.13,<4.14'

# What can be checked without the mount: torch is still the ROCm build pip was not supposed to
# touch, and opencv really has a GUI backend. The rest -- the driver, the patches, the GPU
# itself -- lives in the mount or needs a device, so run.sh checks those with verify_env.py.
RUN python3 -c "import torch, cv2; \
assert 'rocm' in torch.__version__, f'expected a ROCm torch, got {torch.__version__}'; \
gui=next((l.split(':',1)[1].strip() for l in cv2.getBuildInformation().splitlines() if l.strip().startswith('GUI:')), ''); \
assert gui and gui != 'NONE', f'opencv has no GUI support (GUI: {gui})'; \
print('ok:', torch.__version__, '| cv2 GUI:', gui)"

RUN mkdir -p /home/jovyan/lerobot_data /home/jovyan/lerobot_out && \
    chown -R jovyan:1000 /home/jovyan/lerobot_data /home/jovyan/lerobot_out

# The mounted checkout wins over the pip-installed lerobot in site-packages.
ENV PYTHONPATH=/workspace/lerobot/src
# train_pipeline.sh and eval.sh set this themselves; exported so an interactive shell gets it
# too. LD_PRELOAD is deliberately absent -- see SETUP.md.
ENV TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1

USER jovyan
WORKDIR /workspace/lab
