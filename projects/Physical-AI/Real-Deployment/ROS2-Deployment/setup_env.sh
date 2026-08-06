#!/bin/bash
# ============================================================================
# LeKiwi ROS2 Deployment - environment setup (native install on the robot host)
# ============================================================================
# Just run:
#   bash setup_env.sh
# to set up the two userspace pieces this deployment needs:
#   1. conda env `lerobot-new` : ROCm PyTorch + lerobot + depth/analysis deps
#   2. ROS2 Jazzy + Nav2 + RTAB-Map (system apt)
# It is idempotent - safe to re-run; it skips whatever is already installed.
# You can also run just one part:  bash setup_env.sh conda   |   bash setup_env.sh ros2
#
# PREREQUISITE (install yourself first): the AMD OEM kernel + ROCm 7.13 for gfx1152
# (host-level, needs a reboot - see lekiwi_ros2/README.md "Stage 0").
#   verify:  rocminfo | grep gfx1152   &&   amd-smi version
#
# Tunable versions (override via environment variables):
TORCH_SPEC="${TORCH_SPEC:-torch==2.11.0+rocm7.13.0 torchvision==0.26.0+rocm7.13.0}"
TORCH_INDEX="${TORCH_INDEX:-https://repo.amd.com/rocm/whl/gfx1152/}"
CONDA_ENV="${CONDA_ENV:-lerobot-new}"
LEROBOT_DIR="${LEROBOT_DIR:-$HOME/lerobot}"          # editable lerobot checkout, if present
ROS_DISTRO="${ROS_DISTRO:-jazzy}"
# ----------------------------------------------------------------------------

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

banner() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

source_conda() {
    local base
    base="$(conda info --base 2>/dev/null)" || return 1
    # shellcheck disable=SC1091
    source "$base/etc/profile.d/conda.sh" 2>/dev/null || return 1
}

# ---------------------------------------------------------------------------
# conda env `lerobot-new`: ROCm PyTorch + lerobot + depth/analysis deps.
# ---------------------------------------------------------------------------
stage_conda() {
    banner "conda env '$CONDA_ENV' (ROCm torch + lerobot + deps)"
    if ! command -v conda >/dev/null 2>&1; then
        echo "conda not found - install Miniconda/Anaconda first, then re-run." >&2
        exit 1
    fi
    source_conda || { echo "could not source conda.sh" >&2; exit 1; }

    if conda env list | grep -qE "^\s*$CONDA_ENV\s"; then
        echo "conda env '$CONDA_ENV' already exists - reusing it"
    else
        echo "creating conda env '$CONDA_ENV' (python 3.12) ..."
        conda create -n "$CONDA_ENV" python=3.12 -y
    fi
    conda activate "$CONDA_ENV"

    # lerobot (with Feetech motor support): editable from a local checkout if present.
    if python -c "import lerobot" 2>/dev/null; then
        echo "lerobot already importable - skipping"
    elif [ -f "$LEROBOT_DIR/pyproject.toml" ]; then
        echo "installing lerobot (editable) from $LEROBOT_DIR ..."
        pip install -e "$LEROBOT_DIR[feetech]"
    else
        echo "installing lerobot[feetech] from PyPI (no checkout at $LEROBOT_DIR) ..."
        pip install "lerobot[feetech]>=0.5.0"
    fi

    # gfx1152-native ROCm PyTorch wheels (no HSA override needed).
    echo "installing ROCm PyTorch: $TORCH_SPEC"
    pip install --index-url "$TORCH_INDEX" $TORCH_SPEC

    # Depth pipeline (RAFT-Stereo) + notebook analysis deps.
    pip install scipy matplotlib opt_einsum scikit-image opencv-python pyyaml

    # Stop a CPU-only torch in ~/.local from shadowing the ROCm build.
    conda env config vars set PYTHONNOUSERSITE=1 -n "$CONDA_ENV" >/dev/null

    python -c "import torch; print('  torch', torch.__version__, '| cuda:', torch.cuda.is_available())" \
        || echo "  (torch import failed - re-check the ROCm wheels)"
}

# ---------------------------------------------------------------------------
# ROS2 Jazzy + Nav2 + RTAB-Map (system apt).
# ---------------------------------------------------------------------------
stage_ros2() {
    banner "ROS2 $ROS_DISTRO + Nav2 + RTAB-Map (uses sudo)"
    if [ -f "/opt/ros/$ROS_DISTRO/setup.bash" ] && dpkg -l "ros-$ROS_DISTRO-navigation2" >/dev/null 2>&1; then
        echo "ROS2 $ROS_DISTRO + Nav2 already installed - skipping"
        return 0
    fi

    if [ ! -f /etc/apt/sources.list.d/ros2.list ] && ! apt-cache policy 2>/dev/null | grep -q packages.ros.org; then
        sudo apt install -y software-properties-common curl
        sudo add-apt-repository universe -y
        sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
            -o /usr/share/keyrings/ros-archive-keyring.gpg
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo "$UBUNTU_CODENAME") main" \
            | sudo tee /etc/apt/sources.list.d/ros2.list >/dev/null
        echo "  added ROS2 apt repo"
    fi

    sudo apt update
    sudo apt install -y \
        "ros-$ROS_DISTRO-desktop" \
        "ros-$ROS_DISTRO-navigation2" \
        "ros-$ROS_DISTRO-nav2-bringup" \
        "ros-$ROS_DISTRO-rtabmap-ros" \
        v4l-utils

    echo "  the conda python imports rclpy directly; demo scripts export the ROS2"
    echo "  site-packages onto PYTHONPATH (or: export PYTHONPATH=/opt/ros/$ROS_DISTRO/lib/python3.12/site-packages:\$PYTHONPATH)"
}

usage() {
    awk 'NR>=2 { if ($0 !~ /^#/) exit; sub(/^# ?/, ""); print }' "$0"
}

# ---------------------------------------------------------------------------
case "${1:-}" in
    ""|all)       stage_conda; stage_ros2; banner "Done - activate with: conda activate $CONDA_ENV" ;;
    conda)        stage_conda ;;
    ros2)         stage_ros2 ;;
    -h|--help|help) usage ;;
    *) echo "unknown command: $1"; echo; usage; exit 1 ;;
esac
