#!/bin/bash
RD_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"  # repo-relative root
# ===== DEMO 0-v2: SGBM 2D AUTONOMOUS EXPLORATION (real-time, no RAFT) =====
# The 2D-scan pipeline (ZED -> StereoSGBM depth -> /scan -> slam_toolbox /map) driven
# autonomously: Nav2 (costmaps off /scan + /map) + auto_explore.py frontier exploration.
# Much cleaner/lighter than the RTAB point-cloud version -> Nav2 planning should jam less.
#
# Usage (from ros2/):
#   bash demo0-v2/autoexplore_2d.sh              # explore + scan-spin, save map at end
#   bash demo0-v2/autoexplore_2d.sh --no-spin    # no per-waypoint spin (faster)
#   bash demo0-v2/autoexplore_2d.sh --no-rviz    # headless
#   bash demo0-v2/autoexplore_2d.sh --no-save    # don't save the map at the end
# Put the robot at its intended MAP ORIGIN before starting.
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=0
export DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000
export XAUTHORITY=$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1)
R="$RD_ROOT"
V2=$R/demo0-v2
CONDA='source ~/miniconda3/etc/profile.d/conda.sh && conda activate lerobot-new && export PYTHONNOUSERSITE=1 && export PYTHONPATH=/opt/ros/jazzy/lib/python3.12/site-packages:$PYTHONPATH'
CLEAN="bash $R/utils/lekiwi_cleanup.sh"
LOG=/tmp/lekiwi_autoexplore2d; mkdir -p $LOG

DO_RVIZ=1; DO_SAVE=1; SPIN="--scan-spin"; EXTRA=""
for a in "$@"; do case "$a" in
  --no-rviz) DO_RVIZ=0 ;;
  --no-save) DO_SAVE=0 ;;
  --no-spin) SPIN="" ;;
  --spin)    SPIN="--scan-spin" ;;
  *)         EXTRA="$EXTRA $a" ;;
esac; done

trap 'echo; echo ">>> stopping + cleaning up..."; $CLEAN; echo "done."; exit 0' INT TERM

echo ">>> pre-clean (robot must be at the map origin!)"; $CLEAN; sleep 1
ZED_DEV=$(v4l2-ctl --list-devices 2>/dev/null | grep -A1 "ZED" | grep -oE "video[0-9]+" | head -1 | tr -d 'video'); ZED_DEV=${ZED_DEV:-0}
echo ">>> ZED on /dev/video$ZED_DEV"
if [ -f "$V2/cam_settings.conf" ]; then
  . "$V2/cam_settings.conf"; echo ">>> applying saved cam settings: $V4L2_CTRLS"
  v4l2-ctl -d /dev/video$ZED_DEV --set-ctrl="$V4L2_CTRLS" 2>/dev/null || echo "    (some ctrls skipped)"
fi

echo ">>> [1/7] base driver (+record odom not needed; /cmd_vel + /odom + TF)"
bash -c "$CONDA && python $R/utils/lekiwi_base_ros.py" >$LOG/base.log 2>&1 &

echo ">>> [2/7] ZED SGBM depth (real-time, ~15 FPS)"
# uniqueness 6 (was 10): accept more (fainter) matches -> denser scan -> more directions clear free
# space -> whiter/fuller map. Noisier, but explore_lite-py + slam tolerate it now.
bash -c "$CONDA && python $V2/zed_sgbd_scan.py --dev $ZED_DEV --max-depth 2.5 --uniqueness 6 --speckle-win 250 --num-disp 96" >$LOG/zed.log 2>&1 &
until grep -q streaming $LOG/zed.log 2>/dev/null; do sleep 1; done; echo "    SGBM streaming."

echo ">>> [3/7] static TF base_link -> camera_link (scan origin AT base centre)"
# x=0.0 (not the true 0.14 m camera offset): puts the LaserScan origin at the base centre so the
# scan-origin cell — i.e. the robot's start (0,0) — is marked FREE and lands INSIDE the map. With
# x=0.14 the map started 0.14 m ahead of the robot, leaving (0,0) off-grid -> planner rejected the
# start instantly (every goal + return-home failed). 0.14 m is negligible for a room-scale 2D map.
ros2 run tf2_ros static_transform_publisher \
  --x 0.0 --y 0.0 --z 0.11 --roll 0.0 --pitch 0.0 --yaw 0.0 \
  --frame-id base_link --child-frame-id camera_link >$LOG/tf.log 2>&1 &
sleep 1

echo ">>> [4/7] depthimage_to_laserscan (-> /scan)"
ros2 run depthimage_to_laserscan depthimage_to_laserscan_node --ros-args \
  -r depth:=/zed/depth/image -r depth_camera_info:=/zed/rgb/camera_info -r scan:=/scan \
  -p output_frame:=camera_link -p range_min:=0.3 -p range_max:=2.5 -p scan_height:=30 -p scan_time:=0.033 \
  >$LOG/d2s.log 2>&1 &
sleep 2

echo ">>> [5/7] slam_toolbox (2D SLAM -> /map + map->odom)"
ros2 launch slam_toolbox online_async_launch.py \
  slam_params_file:=$V2/slam_toolbox_2d.yaml use_sim_time:=false >$LOG/slam.log 2>&1 &
sleep 6
pgrep -f async_slam_toolbox_node >/dev/null && echo "    slam_toolbox active." || { echo "    !! slam died"; cat $LOG/slam.log; }

echo ">>> [6/7] Nav2 (2D scan costmaps, live /map)"
ros2 launch $V2/nav2_explore_2d.launch.py >$LOG/nav2.log 2>&1 &
until grep -q "Managed nodes are active" $LOG/nav2.log 2>/dev/null; do sleep 1; done; echo "    Nav2 active."

if [ $DO_RVIZ -eq 1 ]; then
  echo ">>> [7/7] RViz"
  rviz2 -d /opt/ros/jazzy/share/nav2_bringup/rviz/nav2_default_view.rviz >$LOG/rviz.log 2>&1 & sleep 3
else
  echo ">>> [7/7] RViz skipped (--no-rviz)"
fi

echo; echo "=========== AUTONOMOUS EXPLORATION (explore_lite-py, Ctrl-C to abort) ==========="
# explore_lite-style Python explorer: continuous re-evaluation + retry-then-blacklist, never gives
# up after a fixed patience -> rides out the transient map->odom transform aborts. (This replaces
# auto_explore.py for the 2D pipeline; --no-spin is irrelevant here so $SPIN is dropped.)
bash -c "$CONDA && python3 -u $V2/explore_lite_py.py --map-topic /map $EXTRA"
echo ">>> exploration finished."

if [ $DO_SAVE -eq 1 ]; then
  echo ">>> saving 2D map -> $R/utils/scan_map.{pgm,yaml}"
  ros2 run nav2_map_server map_saver_cli -f $R/utils/scan_map --ros-args -p save_map_timeout:=10.0 >$LOG/save.log 2>&1 \
    && echo "    saved." || { echo "    !! save failed"; tail -3 $LOG/save.log; }
fi

echo ">>> tearing down..."; $CLEAN; sleep 1; echo "done."
