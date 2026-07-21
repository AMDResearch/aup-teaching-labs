<!-- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved. -->

# ROS2 Deployment - LeKiwi Autonomous Navigation

Deploying a **real** robot: SLAM mapping and autonomous navigation on a **LeKiwi**
omni-directional base + **ZED 2i** stereo camera, on an **AMD-only** machine
(Radeon gfx1152, ROCm - no NVIDIA/CUDA). No ZED SDK: raw stereo -> **RAFT-Stereo**
depth on ROCm -> **RTAB-Map** mapping/localization -> **Nav2** -> wheels.

Each notebook follows the course (MT06) format: markdown explains a step, then a
**runnable code cell defines the function** doing the work and runs it. The
scene-specific cells read **your own** data - the map *you* build, *your* recorded
session, *your* `odom.txt` - so **every student's result is different**. The repo
ships **only the LeKiwi ROS2 code** (`lekiwi_ros2/`, repo-relative so it runs from a
clone); it deliberately ships **no pre-built map and no example run** - you produce
those on the robot, and until you do, the analysis cells print a short "run the robot
step first" hint (they never show a canned result).

## Labs

- **`ROS01_Mapping_Manual`** - build the map by **driving** the LeKiwi with the keyboard,
  then `build_map.sh`: RAFT-Stereo depth -> RTAB-Map ray-traced grid -> locate + mark
  blocks. Defines & runs `disparity_to_depth`, `occupancy_to_pgm`, `detect_color_blocks`,
  and shows the map *you* built.
- **`ROS02_Mapping_AutoExplore`** - the same map, but the robot **explores autonomously**.
  Defines & runs `find_frontiers` + `pick_best_frontier` on *your* map.
- **`ROS03_Click_Navigation`** - RTAB-Map **localization** + Nav2; click **Nav2 Goal** in
  RViz and the LeKiwi drives there. Defines & runs `body_to_wheel` (twist -> wheels) and
  `integrate_odometry` on *your* recorded drive.
- **`ROS04_Color_Navigation`** - name a colour and the robot drives to that block, facing it.
  Defines & runs `snap_to_free` + `block_to_goal` on *your* map + block waypoints.

Recommended order: **ROS01 or ROS02** (build your map) -> **ROS03** (navigate) -> **ROS04**
(task-driven navigation).

## Running the notebooks

The analysis / function cells run in a Jupyter kernel with `numpy`, `opencv-python`,
`pyyaml`, `matplotlib` (all in the `lerobot-new` env; add `jupyterlab`). On a fresh
clone they run top-to-bottom and print the "run the robot first" hints; after you have
recorded + built on the robot, re-run them to see **your** map, depth, trajectory, and
block goals. The steps that drive the physical robot are the `bash` commands shown in
each notebook (run them on the LeKiwi); their full implementations are in `lekiwi_ros2/`.

## `lekiwi_ros2/` - the deployment code

The LeKiwi ROS2 project the notebooks walk through and launch (paths are relative to
each script, so it works from a clone). One-time robot setup - `bash
lekiwi_ros2/utils/setup_raft.sh` (RAFT-Stereo weights) and `bash
lekiwi_ros2/utils/get_calibration.sh <ZED serial>` (your camera's calibration) - and the
full environment notes are in `lekiwi_ros2/README.md`.

## Environment

OS Ubuntu 24.04 - ROCm 7.13 - PyTorch 2.11 (gfx1152) - **ROS2 Jazzy** + Nav2 + RTAB-Map -
conda env `lerobot-new`.

> **Origin.** Every navigation run assumes the robot starts at the same position and
> heading where your ROS01/ROS02 recording began. Mark it on the floor and always start
> there, or the whole map is shifted.

---

Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved. Portions of this file consist of AI-generated content.
SPDX-License-Identifier: MIT
