#!/bin/bash
RD_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"  # repo-relative root
# ============ DEMO 0 (offline): build the map from a recorded session ============
# After recording with lekiwi_map_gui.py, this produces everything demo1/demo2 need:
#   utils/scene_map.pgm/.yaml   (2D occupancy grid, with blocks marked as obstacles)
#   utils/block_waypoints.txt   (colour block positions in the map frame)
#
# Usage (from ros2/):   bash demo0/build_map.sh demo0/rec/map20260704_114742 [--step 10]
set -e
R="$RD_ROOT"
SESS="$1"; shift || true
STEP=10; [ "$1" = "--step" ] && STEP="$2"
[ -d "$SESS" ] || { echo "usage: bash demo0/build_map.sh <session_dir> [--step N]"; echo "sessions:"; ls -d $R/demo0/rec/map* 2>/dev/null; exit 1; }
SESS=$(readlink -f "$SESS")
CONDA='source ~/miniconda3/etc/profile.d/conda.sh && conda activate lerobot-new && export PYTHONNOUSERSITE=1'
CLEAN="bash $R/utils/lekiwi_cleanup.sh"
echo ">>> building map from $SESS (step=$STEP)"

# --- Stage B: batch depth (conda / ROCm) ---
cp -n "$SESS/camera_stamps.txt" "$SESS/stamps.txt" 2>/dev/null || true
echo ">>> [1/4] RAFT-Stereo depth (this takes several minutes)"
bash -c "$CONDA && python $R/demo0/zed_batch_depth.py --rec '$SESS' --step $STEP --iters 32" 2>&1 | grep -viE "FutureWarning|autocast|meshgrid|_VF|return _VF" | tail -3

# --- Stage C: RTAB-Map builds the graph + RAY-TRACED /map from RGB-D + wheel odom ---
echo ">>> [2/4] RTAB-Map graph (replay) + capture ray-traced /map"
$CLEAN >/dev/null 2>&1
source /opt/ros/jazzy/setup.bash; export ROS_DOMAIN_ID=0
bash $R/demo0/run_rtabmap_wheelodom.sh >/tmp/build_rtabmap.log 2>&1 &
until grep -q "subscribed to" /tmp/build_rtabmap.log 2>/dev/null; do sleep 1; done; sleep 2
# grid grabber runs CONCURRENTLY so it captures /map WHILE rtabmap publishes it. `exec python`
# so SIGINT reaches python (not the bash wrapper); grab_grid saves the latest grid from memory
# on SIGINT, so rtabmap can be torn down right after.
bash -c "$CONDA && export PYTHONPATH=/opt/ros/jazzy/lib/python3.12/site-packages:\$PYTHONPATH && exec python $R/demo0/grab_grid.py --out $R/utils/scene_map --odom '$SESS/odom.txt'" >/tmp/build_grab.log 2>&1 &
GRAB_PID=$!
# The replay node stays ALIVE after publishing all frames (it waits for Ctrl-C so RTAB-Map can
# keep the graph), so it must run in the BACKGROUND — foregrounding it hangs build_map.sh here
# forever. Detect completion from its log instead.
bash -c "$CONDA && export PYTHONPATH=/opt/ros/jazzy/lib/python3.12/site-packages:\$PYTHONPATH && python $R/demo0/stage_c_replay_odom.py --data '$SESS/depth_out' --odom '$SESS/odom.txt' --rate 4" >/tmp/build_replay.log 2>&1 &
until grep -q "replay finished" /tmp/build_replay.log 2>/dev/null; do sleep 1; done
echo "    replay done; letting RTAB-Map finish processing..."; sleep 8

# --- Stage D: save the ray-traced grid (SIGINT grabber -> writes scene_map), then stop all ---
echo ">>> [3/4] save ray-traced grid -> utils/scene_map"
kill -INT $GRAB_PID 2>/dev/null || true
until grep -qE "saved|nothing saved|no /map" /tmp/build_grab.log 2>/dev/null; do sleep 1; done
tail -1 /tmp/build_grab.log
$CLEAN >/dev/null 2>&1; sleep 2

# --- Stage E: locate colour blocks + mark them as obstacles ---
echo ">>> [4/4] locate colour blocks + mark as obstacles"
bash -c "$CONDA && python $R/demo0/block_localize.py --session '$SESS'" 2>&1 | grep -E "偵測|red|blue|green|purple|saved" | tail -6
bash -c "$CONDA && python $R/demo0/mark_blocks.py" 2>&1 | tail -2

echo
echo "=================================================================="
echo "  MAP BUILT ->  utils/scene_map.pgm/.yaml  +  utils/block_waypoints.txt"
echo "  now run demo1 (click) or demo2 (colour)."
echo "=================================================================="
