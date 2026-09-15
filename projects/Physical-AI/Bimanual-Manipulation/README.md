<!-- Copyright (C) 2025 Advanced Micro Devices, Inc. All rights reserved. -->

# Bimanual Manipulation Course

[中文](README.zh.md) | **English**

Content for the **Bimanual Manipulation** course (image `auplc-bimanual`: PyTorch
ROCm + lerobot + the XLeRobot driver). A real two-armed robot — two SO-101 arms, a
pan/tilt head and a three-wheel base, 17 commanded channels — learns to put a cube
into a drawer: the right arm opens it, the left arm moves the cube in, the right
arm closes it. Everything runs on AMD ROCm / Strix Halo (gfx1151).

The interesting decision is that this is **not** one bimanual policy. The task is
recorded as three phases and trained as a chain of single-arm policies, which is
what makes it tractable with a single leader arm and tens of demonstrations rather
than hundreds. `chain_eval.py` then runs the phases back to back on the robot,
handing over between them.

Unlike the simulation courses in this repository, this lab drives physical
hardware: USB serial buses, two cameras, and a GPU, all attached to one container.

## Setup

```bash
git clone https://github.com/amd-weiliao/aup-teaching-labs.git ~/aup-teaching-labs
cd ~/aup-teaching-labs/projects/Physical-AI/Bimanual-Manipulation

./setup_lerobot.sh              # clone lerobot, install the driver, apply the patches
docker build -t auplc-bimanual .
./run.sh --check                # eight environment checks
```

`setup_lerobot.sh` is re-runnable and `--check` reports the state without changing
anything. XLeRobot is not upstream, so a plain lerobot checkout cannot drive this
robot: the driver goes into `src/lerobot/robots/` and four patches teach the
official CLI that the robot exists (`lerobot_patches/README.md` explains each).

The checkout is kept **outside** this repository, at `~/lerobot` by default, and
mounted into the container at run time — so the lerobot source you are actually
running stays readable and editable.

## Labs

Every step runs through `./run.sh`, which starts the container with the hardware
attached. Nothing is installed on the host.

### 1. Record — `./run.sh ./record_phases.sh blue open,close`

One continuous demonstration is written into several datasets. You open the drawer,
press `c` the moment it is open, then close it, press `c` again. `close` therefore
starts from exactly the state `open` ends in — which is the state the open policy
will actually hand over at run time. Recording `close` separately, from a drawer
opened by hand, would train it on states the open policy never produces, and that
mismatch is what breaks chained policies.

Only one leader arm needs to be connected; swap it over and record `pick`
separately. `./run.sh python check_phases.py` checks the result: episode counts,
dimensions, constant channels, cameras.

### 2. Train — `./run.sh ./train_pipeline.sh`

Trim to per-arm dimensions, repair the normalisation statistics, gate on a data
check, then train ten models: one ACT per phase and colour, plus a
language-conditioned SmolVLA over all three colours of the pick phase.

That comparison is the point of the project — three specialists each shown 18
demonstrations of a single drawer, against one model shown all 54 and told which
drawer in words.

### 3. Evaluate — `./run.sh python chain_eval.py --stages ... --colour blue`

Connects once, swaps policies between phases, masks the idle arm and ramps between
poses at hand-over. `./run.sh ./eval.sh blue 60` runs a single phase through the
official `lerobot-rollout` path instead.

## Why `run.sh` exists

The container needs a GPU, two USB serial buses, two cameras and an X server, and
each flag fails differently when it is missing:

| flag | what happens without it |
|---|---|
| `--group-add render` | `/dev/kfd` and `/dev/dri/renderD*` belong to the render group; without it `torch.cuda.is_available()` is False and nothing says why |
| `--group-add dialout` | `/dev/ttyACM*` belong to dialout and the serial ports cannot be opened |
| `-v /dev/serial`, `-v /dev/v4l` | `--device` passes the character device but not the by-id / by-path symlinks, and every script addresses hardware by those stable names rather than by `ttyACM0` |
| `-e DISPLAY`, `-v /tmp/.X11-unix` | `cv2.imshow` has no X server (under Wayland this is XWayland, which is why `DISPLAY` is `:0`) |

Devices are attached only when present, so the container still starts with the
robot unplugged; training needs none of them.

Two directories are mounted: this lab at `/workspace/lab` (the working directory)
and the lerobot checkout at `/workspace/lerobot`. `PYTHONPATH` puts the mounted
source ahead of the copy installed in the image, so the patched code is what runs
and edits on the host take effect without a rebuild. `~/lerobot_data` and
`~/lerobot_out` are mounted for datasets and checkpoints.

## Notes

**No torch is installed in the image.** The base image ships one built against
AMD's rocm-sdk wheels (`2.9.1+rocm7.13.0`) and lerobot's declared range accepts it.
lerobot is installed from the same commit `setup_lerobot.sh` checks out, not from
PyPI: the two disagree about dependencies at an identical version number, and the
mismatch is silent — training completes and reproduces its losses exactly while
writing checkpoints that cannot be loaded back. `verify_env.py` compares the
running source's own pins against what is installed, which is what catches this.

**Datasets are image-backed.** Recording always passes `--images-not-video`:
torchcodec 0.11.1 needs torch 2.11 or newer, so the container cannot read
video-backed datasets. Frames are embedded as PNG in the parquet, at a cost of
roughly 39 KB per frame.

**`LD_PRELOAD` must be empty.** The rocm-sdk wheels carry their own HSA runtime;
preloading a system one makes `import torch` die on an undefined symbol that looks
like a broken install. `TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1` is set in the
image.

**Recording needs no GPU**, evaluation does. On CPU, ACT costs about 1.9 s per
inference and SmolVLA about 40 s, against chunks worth 6.7 s and 3.3 s of motion.

## Files

| file | purpose |
|---|---|
| `setup_lerobot.sh` | prepares the lerobot checkout: pinned commit, driver, four patches (`--check` only reports) |
| `Dockerfile` · `run.sh` | the environment, and the launcher that attaches the hardware to it |
| `verify_env.py` | eight environment checks, called by `train_pipeline.sh` and `docker build` |
| `record_phases.sh` · `record_teleop.py` · `stdin_keyboard.py` | phase recording, teleoperation, and a Wayland-safe keyboard reader |
| `check_phases.py` · `delete_episodes.py` | dataset quality check, and episode deletion with renumbering |
| `train_pipeline.sh` · `trim_dims.py` · `fix_constant_stats.py` · `verify_arms.py` | the training pipeline and its preparation and gating steps |
| `chain_eval.py` · `eval.sh` · `view_cams.py` | chained evaluation, single-phase evaluation, camera preview |
| `lerobot_patches/` | the XLeRobot driver and the four patches applied to lerobot |
| `environment.yml` · `build_env.sh` | conda environment for running on the host without the container (see `SETUP.md`) |

`SETUP.md` covers the hardware wiring, the traps found along the way and the
physical root causes behind them. Chinese translations are in `README.zh.md` and
`SETUP.zh.md`.
