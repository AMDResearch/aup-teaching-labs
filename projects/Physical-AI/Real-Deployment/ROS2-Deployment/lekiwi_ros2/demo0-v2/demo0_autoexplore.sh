#!/bin/bash
RD_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"  # repo-relative root
# ============ DEMO 0-v2: AUTONOMOUS EXPLORATION mapping (no keyboard driving) ============
# The robot explores the scene by ITSELF (frontier exploration on a live RTAB-Map + Nav2),
# recording raw stereo + wheel odom the whole time. When done it returns to the origin,
# then the recorded session is rebuilt OFFLINE by build_map.sh (iters=32) into the same
# high-quality utils/scene_map.* + block_waypoints.txt that demo1/demo2 consume.
#
# Usage (from ros2/):
#   bash demo0-v2/demo0_autoexplore.sh                 # explore + scan-spin + auto build_map
#   bash demo0-v2/demo0_autoexplore.sh --no-build      # explore only (build later yourself)
#   bash demo0-v2/demo0_autoexplore.sh --no-rviz       # headless
#   bash demo0-v2/demo0_autoexplore.sh --no-spin       # skip per-waypoint scan rotation (faster)
# Put the robot at its intended MAP ORIGIN before starting (demo1/2 assume this start pose).
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=0
export DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000
export XAUTHORITY=$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1)
R="$RD_ROOT"
V2=$R/demo0-v2
CONDA='source ~/miniconda3/etc/profile.d/conda.sh && conda activate lerobot-new && export PYTHONNOUSERSITE=1 && export PYTHONPATH=/opt/ros/jazzy/lib/python3.12/site-packages:$PYTHONPATH'
CLEAN="bash $R/utils/lekiwi_cleanup.sh"
LOG=/tmp/lekiwi_demo0v2; mkdir -p $LOG

# Look-around ON by default. It rotates in place at each SAFE stand-off waypoint via DIRECT
# /cmd_vel (not Nav2's spin, which jammed on the costmap), then clears the local costmap so the
# spin's false obstacles don't block the next leg. This gives fuller coverage. --no-spin for
# pure translation.
DO_BUILD=1; DO_RVIZ=1; SPIN="--scan-spin"; EXTRA=""
for a in "$@"; do case "$a" in
  --no-build) DO_BUILD=0 ;;
  --no-rviz)  DO_RVIZ=0 ;;
  --spin)     SPIN="--scan-spin" ;;
  --no-spin)  SPIN="" ;;
  *)          EXTRA="$EXTRA $a" ;;
esac; done

trap 'echo; echo ">>> stopping + cleaning up..."; $CLEAN; echo "done."; exit 0' INT TERM

SESS=$V2/rec/map$(date +%Y%m%d_%H%M%S)
mkdir -p "$SESS/frames"
echo ">>> session: $SESS"
echo ">>> pre-clean (robot must be at the map origin!)"; $CLEAN; sleep 1
ZED_DEV=$(v4l2-ctl --list-devices 2>/dev/null | grep -A1 "ZED" | grep -oE "video[0-9]+" | head -1 | tr -d 'video'); ZED_DEV=${ZED_DEV:-0}
echo ">>> ZED on /dev/video$ZED_DEV"

# Apply saved camera tuning (from demo0-v2/cam_tune.py) BEFORE the node opens the camera.
if [ -f "$V2/cam_settings.conf" ]; then
  . "$V2/cam_settings.conf"
  echo ">>> applying saved cam settings: $V4L2_CTRLS"
  v4l2-ctl -d /dev/video$ZED_DEV --set-ctrl="$V4L2_CTRLS" 2>/dev/null || echo "    (some ctrls skipped)"
fi

echo ">>> [1/6] base driver (+record odom)"
bash -c "$CONDA && python $R/utils/lekiwi_base_ros.py --record '$SESS'" >$LOG/base.log 2>&1 &

echo ">>> [2/6] ZED live depth (+record frames, warming up RAFT ~80s)"
# --max-depth 3.0 : far VGA depth is noise; keep only what the costmap (obstacle_max_range 2.5) uses.
# --depth-filter  : drop low-confidence (textureless/occluded) depth so it can't box the robot in.
# --iters 16      : slow exploration can afford sharper disparity than the demo1/2 live default (10).
# NOTE: only the LIVE navigation costmap is affected; the final map is rebuilt offline (iters=32).
bash -c "$CONDA && python $R/utils/zed_rgbd_ros2.py --dev $ZED_DEV --iters 16 --max-depth 3.0 --depth-filter --tex-thresh 11 --record '$SESS'" >$LOG/zed.log 2>&1 &
until grep -q streaming $LOG/zed.log 2>/dev/null; do sleep 2; done; echo "    ZED streaming."

echo ">>> [3/6] RTAB-Map LIVE mapping (growing /map)"
bash -c "bash $V2/run_rtabmap_mapping.sh" >$LOG/rtabmap.log 2>&1 &
# Wait via the LOG, not the ros2 CLI: a stale ros2 daemon can make `ros2 topic list` return
# nothing even while rtabmap is publishing, which used to hang this step forever.
# "rtabmap (N):" ticks appear once it has RGB-D+odom and is actually building the map.
until grep -qE "rtabmap \(|subscribed to" $LOG/rtabmap.log 2>/dev/null; do sleep 1; done; sleep 3
echo "    RTAB-Map mapping — /map is live."

echo ">>> [4/6] Nav2 (explore config, live map)"
bash -c "source /opt/ros/jazzy/setup.bash && export ROS_DOMAIN_ID=0 && ros2 launch $V2/nav2_explore.launch.py" >$LOG/nav2.log 2>&1 &
until grep -q "Managed nodes are active" $LOG/nav2.log 2>/dev/null; do sleep 1; done; echo "    Nav2 active."

if [ $DO_RVIZ -eq 1 ]; then
  echo ">>> [5/6] RViz (watch the map grow)"
  rviz2 -d /opt/ros/jazzy/share/nav2_bringup/rviz/nav2_default_view.rviz >$LOG/rviz.log 2>&1 & sleep 3
else
  echo ">>> [5/6] RViz skipped (--no-rviz)"
fi

echo; echo "=========== AUTONOMOUS EXPLORATION (explore_lite-py, Ctrl-C to abort) ==========="
# explore_lite-py: persistent frontier explorer (retry-then-blacklist + watchdog, never gives up
# after a fixed patience like auto_explore.py did). Reads RTAB's /map + drives Nav2. No scan-spin.
bash -c "$CONDA && python3 -u $V2/explore_lite_py.py --map-topic /map --initial-spin --min-frontier 5 $EXTRA"
echo ">>> exploration finished."

echo ">>> tearing down live nodes..."; $CLEAN; sleep 2
FRAMES=$(ls "$SESS/frames" 2>/dev/null | wc -l); ODOM=$(grep -vc '^#' "$SESS/odom.txt" 2>/dev/null || echo 0)
echo ">>> recorded: $FRAMES frames, $ODOM odom lines -> $SESS"

if [ $DO_BUILD -eq 1 ]; then
  echo; echo ">>> [6/6] building high-quality map offline (iters=32, ~10-15 min)..."
  bash $R/demo0/build_map.sh "$SESS"
else
  echo; echo ">>> [6/6] build skipped (--no-build). Build later with:"
  echo "        bash demo0/build_map.sh $SESS"
fi
