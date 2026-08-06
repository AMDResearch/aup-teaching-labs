<!-- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved. -->

# ROS2 Deployment — LeKiwi Autonomous Navigation

Deploying a **real** robot: SLAM mapping and autonomous navigation on a **LeKiwi**
omni-directional base + **ZED 2i** stereo camera, on an **AMD-only** machine (Radeon
gfx1152, ROCm — no NVIDIA/CUDA). No ZED SDK: raw stereo → **RAFT-Stereo** depth on
ROCm → **RTAB-Map** mapping/localization → **Nav2** → wheels.

Each notebook follows the course (MT06) format: markdown explains a step, then a
runnable code cell **defines the function** doing the work and runs it. The
scene-specific cells read **your own** data — the map *you* build, *your* recorded
session, *your* `odom.txt` — so every student's result is different. The repo ships
**only the LeKiwi ROS2 code** (`lekiwi_ros2/`, repo-relative so it runs from a clone);
it deliberately ships **no pre-built map and no example run** — you produce those on
the robot, and until you do, the analysis cells print a short "run the robot step
first" hint (never a canned result).

## Labs

| Notebook | Topic |
|---|---|
| **`ROS01_Mapping_Manual`** | Build the map by **driving** the LeKiwi (keyboard), then `build_map.sh`: RAFT-Stereo depth → RTAB-Map grid → locate + mark blocks. Defines & runs `disparity_to_depth`, `occupancy_to_pgm`, `detect_color_blocks`. |
| **`ROS02_Mapping_AutoExplore`** | The same map, but the robot **explores autonomously**. Defines & runs `find_frontiers` + `pick_best_frontier`. |
| **`ROS03_Click_Navigation`** | RTAB-Map **localization** + Nav2; click **Nav2 Goal** in RViz and the LeKiwi drives there. Defines & runs `body_to_wheel` + `integrate_odometry`. |
| **`ROS04_Color_Navigation`** | Name a colour and the robot drives to that block, facing it. Defines & runs `snap_to_free` + `block_to_goal`. |

Recommended order: **ROS01 or ROS02** (build your map) → **ROS03** (navigate) → **ROS04**
(task-driven navigation).

## Prerequisites

- **LeKiwi** omni-directional base (3× Feetech STS3215 wheels on `/dev/ttyACM0`)
- **ZED 2i** stereo camera (USB UVC — no ZED SDK used)
- An **AMD Ryzen AI** machine (Radeon **gfx1152**, RDNA3.5)
- A display (Nav2 goals are clicked in **RViz**)

## Environment setup

This stack is a **native install on the robot host** (an AMD OEM kernel, ROCm from
apt, system ROS2 Jazzy, real hardware, and RViz) — none of which fits cleanly in a
container, so setup is a script you run on the robot rather than a Dockerfile.

**Prerequisite — install yourself first:** the AMD OEM kernel + ROCm 7.13 for
**gfx1152** (host-level, needs a reboot). The exact apt commands are in
[`lekiwi_ros2/README.md`](lekiwi_ros2/README.md) ("Stage 0"). Verify with
`rocminfo | grep gfx1152`.

**Then just run:**

```bash
bash setup_env.sh          # conda env lerobot-new (ROCm PyTorch + lerobot + deps) + ROS2 Jazzy/Nav2/RTAB-Map
```

It is idempotent (safe to re-run — it skips whatever is already installed) and you
can do one part at a time with `bash setup_env.sh conda` or `bash setup_env.sh ros2`.
Version pins (PyTorch wheels, ROS distro) are variables at the top of the script.
RAFT-Stereo weights are a one-time `bash lekiwi_ros2/utils/setup_raft.sh`, and your
ZED camera's calibration is fetched from within the notebooks (the ZED serial goes
in the lab).

## Running the labs

```bash
conda activate lerobot-new
cd ROS2-Deployment
jupyter lab
```

Open the four `ROS0*.ipynb` notebooks and work through them in order. The analysis /
function cells run in the kernel; on a fresh clone they print the "run the robot
first" hints, and after you have recorded + built on the robot, re-running them shows
**your** map, depth, trajectory, and block goals. The steps that drive the physical
robot are `bash` commands shown inside each notebook (run them on the LeKiwi); their
full implementations live in `lekiwi_ros2/`.

## Directory layout

```
.
├── setup_env.sh               # environment setup: conda env lerobot-new + ROS2 Jazzy/Nav2/RTAB-Map
├── ROS01_Mapping_Manual.ipynb
├── ROS02_Mapping_AutoExplore.ipynb
├── ROS03_Click_Navigation.ipynb
├── ROS04_Color_Navigation.ipynb
└── lekiwi_ros2/               # the deployment code the notebooks walk through
    ├── utils/                 # base driver, ZED node, Nav2 config, cleanup, setup_raft.sh, get_calibration.sh
    ├── demo0/  demo0-v2/      # mapping (manual drive / autonomous explore)
    ├── demo1/                 # Nav2 click-to-go
    └── demo2/                 # drive to a coloured block
```

Runtime artifacts you produce (RAFT-Stereo checkout + weights, `calib.conf`, the map
`scene_map.*`, `block_waypoints.txt`, recorded sessions) are git-ignored — they are
each student's own and never committed.

## Environment

Ubuntu 24.04 · **6.14 OEM** kernel · **ROCm 7.13** · PyTorch 2.11 (gfx1152 wheel) ·
**ROS2 Jazzy** + Nav2 + RTAB-Map · conda env `lerobot-new`.

> **Origin.** Every navigation run assumes the robot starts at the same position and
> heading where your ROS01/ROS02 recording began. Mark it on the floor and always start
> there, or the whole map is shifted.

---

Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved. Portions of this file consist of AI-generated content.
SPDX-License-Identifier: MIT
