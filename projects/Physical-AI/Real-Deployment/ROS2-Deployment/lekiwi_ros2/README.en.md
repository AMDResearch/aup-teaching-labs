# LeKiwi Autonomous Navigation (ROS2 Jazzy + Nav2 + RTAB-Map)

English | **[中文](README.md)**

Autonomous SLAM mapping & navigation with a **ZED 2i stereo camera + LeKiwi omni-directional base**, on an **AMD-only machine** (no NVIDIA/CUDA).
No ZED SDK required: grab raw stereo → RAFT-Stereo depth on the AMD GPU (ROCm) → RTAB-Map mapping/localization → Nav2.

```
ZED 2i ─(raw UVC stereo)→ RAFT-Stereo depth (ROCm) → RTAB-Map (map + localization) → Nav2 → LeKiwi
                                                            ↑ wheel odometry (motors 7/8/9)
```

> **Run everything from `cd ~/lerobot/lekiwi/ros2`**

## Demos (recorded on the author's setup — click to play)

| Demo 1 — click a goal in RViz | Demo 2 — go to a colored block |
|---|---|
| [![demo1](imgs/demo1_poster.jpg)](imgs/demo1_example.mp4) | [![demo2](imgs/demo2_poster.jpg)](imgs/demo2_example.mp4) |

The 2D map built by demo0 (white = free, black = obstacle, gray = unknown; colored dots = blocks):

<img src="imgs/example_map.png" width="360">

> Shown as illustration only. **After cloning, rebuild the map for your own scene with demo0.**

## Environment Setup

### Tested platform (AMD-only, no NVIDIA/CUDA)

| Item | Version |
|---|---|
| CPU / iGPU | AMD Ryzen AI 7 350 / Radeon 860M (**gfx1152**, RDNA3.5, UMA shared ~30GB RAM) |
| OS | Ubuntu 24.04 |
| Kernel | **6.14 OEM** (`linux-image-6.14.0-10xx-oem`; AMD Ryzen ROCm requires 6.14-1018 OEM or newer) |
| ROCm | **7.13.0** (preview; native gfx1152 support) |
| PyTorch | `torch 2.11.0+rocm7.13.0` (gfx1152-specific wheel, **native, no HSA override**) |
| ROS2 | Jazzy (`/opt/ros/jazzy`) + Nav2 + rtabmap_ros |

### 0. System: AMD OEM kernel + ROCm (official Ryzen APU flow)

```bash
# OEM kernel (gfx1152 needs 6.14-1018 OEM or newer); reboot into it afterwards
sudo apt update && sudo apt install linux-image-6.14.0-1020-oem linux-headers-6.14.0-1020-oem
sudo apt install libatomic1 libquadmath0
# ROCm 7.13 apt source (official gfx1152 support)
sudo mkdir -p /etc/apt/keyrings
wget https://repo.amd.com/rocm/packages/gpg/rocm.gpg -O - | gpg --dearmor | sudo tee /etc/apt/keyrings/amdrocm.gpg >/dev/null
echo 'deb [arch=amd64 signed-by=/etc/apt/keyrings/amdrocm.gpg] https://repo.amd.com/rocm/packages/ubuntu2404 stable main' | sudo tee /etc/apt/sources.list.d/rocm.list
sudo apt update && sudo apt install amdrocm7.13-gfx1152      # gfx1152-specific meta package
sudo usermod -aG render,video,dialout $USER    # re-login for GPU + motor serial port
# verify: rocminfo | grep gfx1152 ; amd-smi version
```

### 1. Conda env `lerobot-new` (ROCm PyTorch + lerobot + depth deps)

```bash
conda create -n lerobot-new python=3.12 -y
conda activate lerobot-new
# lerobot (with Feetech motor support) — from this repo root
cd ~/lerobot && pip install -e ".[feetech]"
# override with the gfx1152-native ROCm PyTorch (ROCm 7.13 per-arch wheel index; no HSA override)
pip install --index-url https://repo.amd.com/rocm/whl/gfx1152/ "torch==2.11.0+rocm7.13.0" "torchvision==0.26.0+rocm7.13.0"
# extras used by RAFT-Stereo depth
pip install scipy matplotlib opt_einsum scikit-image opencv-python pyyaml
```

### 2. ROS2 Jazzy + Nav2 + RTAB-Map

```bash
sudo apt install ros-jazzy-desktop ros-jazzy-navigation2 ros-jazzy-nav2-bringup ros-jazzy-rtabmap-ros v4l-utils
```
> The conda python can `import rclpy` directly (both cpython 3.12) — at runtime add
> `export PYTHONPATH=/opt/ros/jazzy/lib/python3.12/site-packages:$PYTHONPATH` (the demo scripts already do this).

### 3. This repo: RAFT weights + camera calibration

```bash
cd ~/lerobot/lekiwi/ros2
bash utils/setup_raft.sh                      # download RAFT-Stereo + weights (~200MB)
bash utils/get_calibration.sh <your ZED serial>   # download your ZED's factory calib -> utils/calib.conf
```

### Hardware & notes

- Motors on `/dev/ttyACM0`; ZED on `/dev/videoN` (index changes across boots — scripts auto-detect).
- ⚠️ **Before every navigation run, place the robot back at the "origin"** — i.e. the position and heading where the demo0 recording started. demo1/demo2 auto-initialize localization at this origin; if the pose/heading is off, the whole map is shifted. Mark the spot on the floor so you can return to the exact same start each time.

---

## Folders

| Folder | Contents |
|---|---|
| `utils/` | Shared runtime: base driver, ZED live node, Nav2 config/launch, RTAB-Map localization, cleanup, calibration, **the built map**, RAFT-Stereo (link) |
| `demo0/` | Mapping (keyboard driving): record → depth → RTAB-Map → 2D grid → block localization → map in `utils/` |
| `demo0-v2/` | Same, but the robot **explores autonomously** (frontier); auto-chains `demo0/build_map.sh` afterwards |
| `demo1/` | Nav2 basics: click a point in RViz, the robot drives there |
| `demo2/` | Drive to a colored block (red/purple/blue/green) |

**Outputs (produced by demo0, used by demo1/2):** `utils/scene_map.pgm` `utils/scene_map.yaml` (blocks marked as obstacles), `utils/block_waypoints.txt`.

---

## Demo 0: build the map (scan the scene once)

**1) Record** (conda env, drive with WASD, loop the scene and **return to the start**)
```bash
cd ~/lerobot/lekiwi/ros2
source ~/miniconda3/etc/profile.d/conda.sh && conda activate lerobot-new
PYTHONNOUSERSITE=1 python demo0/lekiwi_map_gui.py --cam-dev 0     # window: start/stop recording
```
→ creates `demo0/rec/mapYYYYmmdd_HHMMSS/`

**2) Build the map** (auto: depth → RTAB-Map → 2D grid → block localization → mark obstacles)
```bash
bash demo0/build_map.sh demo0/rec/map20260704_114742          # use your session
```
→ produces `utils/scene_map.*` + `utils/block_waypoints.txt`. Takes ~10-15 min.

---

## Demo 0-v2: autonomous exploration mapping (no keyboard driving)

Same map, but the robot **explores by itself** via frontier exploration — no human driving.
It runs RTAB-Map mapping + Nav2 live; `auto_explore.py` reads the growing `/map`, finds the
known-free ↔ unknown boundary (frontiers), and keeps sending the nearest frontier as a Nav2
goal until the scene is covered, then **returns to the origin**. Raw stereo + odom are recorded
throughout, and when exploration ends it **auto-chains the offline `build_map.sh` (iters=32)** to
produce the same high-quality map as Demo 0.

> The online map is only used to decide where to go — it's throwaway. The final map still comes
> from offline high-quality depth. Moving slowly actually sharpens the depth and loop closures.

```bash
cd ~/lerobot/lekiwi/ros2
bash demo0-v2/demo0_autoexplore.sh                # explore + per-waypoint scan-spin + auto build
bash demo0-v2/demo0_autoexplore.sh --no-spin      # don't rotate in place at each waypoint (faster)
bash demo0-v2/demo0_autoexplore.sh --no-build     # explore/record only; build_map.sh later
bash demo0-v2/demo0_autoexplore.sh --no-rviz      # headless
```
Put the robot at its intended **map origin** first (demo1/2 assume this start pose). `Ctrl-C`
stops + cleans up + halts the motors anytime. ~15-30 min including the offline build.

---

## Demo 1: Nav2 click-to-go

```bash
cd ~/lerobot/lekiwi/ros2
bash demo1/demo1_click_nav.sh
```
In RViz: click the **`Nav2 Goal`** tool → click a point in a white (free) area → drag heading → release; the robot drives there.
`Ctrl-C` auto-cleans everything.

---

## Demo 2: go to a colored block

```bash
cd ~/lerobot/lekiwi/ros2
bash demo2/demo2_color_nav.sh blue            # go to blue
bash demo2/demo2_color_nav.sh green red        # green then red
bash demo2/demo2_color_nav.sh red --standoff 0.05   # stop distance from the block (default 0.1m)
```
The robot stops in front of the block, roughly facing it. `Ctrl-C` auto-cleans.

---

## Cleanup

Stop all nodes + the motors anytime:
```bash
bash utils/lekiwi_cleanup.sh
```

---

## Known limitations / notes

- **VGA resolution** (ZED over USB2; a USB3 blue port gives 720p/1080p → more features, better loop closure).
- Wheel odometry is velocity-integrated and drifts over long / rotation-heavy paths; VGA visual correction is limited → **short, nearby goals are most reliable**.
- gfx1152 is **natively supported** (ROCm 7.13 + matching PyTorch, no HSA override); RAFT's first run compiles kernels (~80s), then ~1-2s/frame.
- Blocks are ~5cm tall, below the depth obstacle band, so `mark_blocks.py` stamps them as obstacles to avoid driving over them.
- System: AMD OEM 6.14 kernel + ROCm 7.13.0.
