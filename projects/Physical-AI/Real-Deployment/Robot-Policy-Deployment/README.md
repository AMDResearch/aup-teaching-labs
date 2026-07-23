<!-- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved. -->

# Robot Policy Deployment — Imitation Learning on a real SO-101 arm

A hands-on lab series for collecting demonstrations and training/deploying robot
manipulation policies on a **real SO-101 arm** with [LeRobot](https://github.com/huggingface/lerobot),
on an **AMD Ryzen AI** machine (Radeon gfx1152, ROCm). You
teleoperate the arm, record **your own** demonstration dataset, then train and run
two kinds of policy on it: **ACT** (trained from scratch) and **SmolVLA** (a
vision-language-action foundation model, finetuned).

This is the real-robot counterpart to the simulation labs in `mujoco-torch` (MT04
behaviour cloning, MT05 SmolVLA) and the sibling of `ROS2-Deployment` (navigation).
Each notebook explains a step, then runs the tool that does the work; the steps that
touch the physical robot or run long (teleop, recording, training) are shown as
commands you run in a **JupyterLab Terminal**. The repo ships the deployment
environment (Dockerfile, `run.sh`, calibration) but **no dataset and no trained
model** — you produce those yourself on the robot, so every student's result differs.

## Labs

| Notebook | Topic |
|---|---|
| **`RPD01_SO101_Teleoperation`** | Identify USB ports, verify calibration, run leader→follower teleoperation. |
| **`RPD02_Data_Collection`** | Detect/preview cameras, record a `LeRobotDataset` of demonstrations. |
| **`RPD03_ACT_Train_and_Eval`** | Train an **ACT** policy from scratch on your data; deploy it autonomously. |
| **`RPD04_SmolVLA_Finetune_and_Eval`** | Finetune **SmolVLA** on the same data; compare ACT vs SmolVLA. |

Recommended order: **RPD01 → RPD02 → RPD03 → RPD04** (each builds on the previous one).

## Prerequisites

- Docker with GPU (ROCm) access
- SO-101 robot arms (leader + follower) connected via USB
- Two USB cameras

## Quick start

### 1. Build the image

```bash
docker build -t lerobot-notebook .
```

### 2. Run the container

```bash
./run.sh
```

This launches JupyterLab at `http://localhost:8888` and mounts this folder into the
container at `/opt/workspace/lerobot`, so notebooks and recorded data persist on the
host. Open the `RPD0*.ipynb` notebooks and start with **RPD01**.

`run.sh` runs the container `--privileged` with `--group-add dialout/video` (USB
serial ports + cameras) and `--shm-size=8g` (PyTorch DataLoader shared memory —
**increase to `16g`/`32g` if training hits out-of-shared-memory errors**).

## Directory layout

```
.
├── Dockerfile                 # ROCm + LeRobot + SmolVLA deps
├── run.sh                     # launch JupyterLab with GPU + USB mounted
├── calibration/               # SO-101 leader/follower calibration JSONs
├── local_data/                # datasets you record (git-ignored; created at runtime)
├── RPD01_SO101_Teleoperation.ipynb
├── RPD02_Data_Collection.ipynb
├── RPD03_ACT_Train_and_Eval.ipynb
└── RPD04_SmolVLA_Finetune_and_Eval.ipynb
```

Trained models land in `models/` and logs in `logs/` (both git-ignored) inside the
container-mounted workspace.

## Environment

Ubuntu 24.04 · ROCm · PyTorch (gfx1152) · [LeRobot](https://github.com/huggingface/lerobot)
v0.5.0+ (`lerobot[feetech]`) · SmolVLA deps (`transformers`, `accelerate`, `num2words`),
all installed in the Docker image.

> ⚠️ Watch the arm during autonomous evaluation and keep the follower's USB cable within
> reach — if the gripper locks, press **Ctrl+C**, then unplug the follower to cut torque
> and release it.

---

Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved. Portions of this file consist of AI-generated content.
SPDX-License-Identifier: MIT
