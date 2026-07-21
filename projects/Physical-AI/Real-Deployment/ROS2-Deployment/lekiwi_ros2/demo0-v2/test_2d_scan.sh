#!/bin/bash
RD_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"  # repo-relative root
# ===== DEMO 0-v2 PROTOTYPE: depth -> LaserScan -> slam_toolbox (2D SLAM) =====
# Alternative to the RTAB point-cloud pipeline: collapse the ZED depth image into a thin
# 2D LaserScan (depthimage_to_laserscan) and run slam_toolbox for a clean 2D map. Far fewer
# phantom obstacles (only a horizontal band is used; textureless floor is already holes after
# the depth filter) and crisp wall lines -> closer to the "closed outline + obstacles" ideal.
#
# This does NOT touch the RTAB demo0-v2 flow. For this first test you DRIVE MANUALLY (slow
# keyboard teleop) and watch /map grow in RViz, to judge whether the 2D map is cleaner.
#
# Usage (from ros2/):   bash demo0-v2/test_2d_scan.sh          # with RViz
#                       bash demo0-v2/test_2d_scan.sh --no-rviz
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=0
export DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000
export XAUTHORITY=$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1)
R="$RD_ROOT"
V2=$R/demo0-v2
CONDA='source ~/miniconda3/etc/profile.d/conda.sh && conda activate lerobot-new && export PYTHONNOUSERSITE=1 && export PYTHONPATH=/opt/ros/jazzy/lib/python3.12/site-packages:$PYTHONPATH'
CLEAN="bash $R/utils/lekiwi_cleanup.sh"
LOG=/tmp/lekiwi_2dscan; mkdir -p $LOG
DO_RVIZ=1; [ "$1" = "--no-rviz" ] && DO_RVIZ=0

trap 'echo; echo ">>> stopping + cleaning up..."; $CLEAN; echo "done."; exit 0' INT TERM

echo ">>> pre-clean (put the robot at its map origin!)"; $CLEAN; sleep 1
ZED_DEV=$(v4l2-ctl --list-devices 2>/dev/null | grep -A1 "ZED" | grep -oE "video[0-9]+" | head -1 | tr -d 'video'); ZED_DEV=${ZED_DEV:-0}
echo ">>> ZED on /dev/video$ZED_DEV"
if [ -f "$V2/cam_settings.conf" ]; then
  . "$V2/cam_settings.conf"; echo ">>> applying saved cam settings: $V4L2_CTRLS"
  v4l2-ctl -d /dev/video$ZED_DEV --set-ctrl="$V4L2_CTRLS" 2>/dev/null || echo "    (some ctrls skipped)"
fi

echo ">>> [1/6] base driver (/cmd_vel -> wheels, /odom + TF)"
bash -c "$CONDA && python $R/utils/lekiwi_base_ros.py" >$LOG/base.log 2>&1 &

echo ">>> [2/6] ZED live depth — REAL-TIME StereoSGBM (~15 FPS, no RAFT, no warmup)"
# SGBM instead of RAFT: ~15 FPS vs ~1.5, so the 2D map updates in real time. A 2D occupancy scan
# only needs coarse min-distance-per-column, which SGBM gives fine. Same topics as zed_rgbd_ros2.py
# (/zed/depth/image + /zed/rgb/camera_info), so depthimage_to_laserscan + slam_toolbox are unchanged.
# Balanced for clean-ish walls: uniqueness 10 (8 caught walls but was noisy, 12 missed walls),
# max-depth 2.5 (far SGBM is noisiest -> kills the radiating fan streaks), speckle-win 250
# (drop bigger noise blobs). Tune uniqueness 8<->12 for wall-coverage vs noise.
bash -c "$CONDA && python $V2/zed_sgbd_scan.py --dev $ZED_DEV --max-depth 2.5 --uniqueness 10 --speckle-win 250" >$LOG/zed.log 2>&1 &
until grep -q streaming $LOG/zed.log 2>/dev/null; do sleep 1; done; echo "    SGBM depth streaming."

echo ">>> [3/6] static TF base_link -> camera_link (x-forward body frame, translation only)"
# depthimage_to_laserscan outputs a HORIZONTAL scan into an x-forward / z-up 'body' frame (its
# default is camera_depth_frame) — NOT the z-forward optical frame. Camera faces forward, level,
# so camera_link == base_link orientation + a forward/up offset (no rotation).
ros2 run tf2_ros static_transform_publisher \
  --x 0.0 --y 0.0 --z 0.11 --roll 0.0 --pitch 0.0 --yaw 0.0 \
  --frame-id base_link --child-frame-id camera_link >$LOG/tf.log 2>&1 &
sleep 1

echo ">>> [4/6] depthimage_to_laserscan (/zed/depth/image -> /scan)"
# scan_height: how many image rows (a horizontal band) collapse into one scan line. min-depth per
# column. The depth filter already voids textureless floor, so floor false-positives are limited.
ros2 run depthimage_to_laserscan depthimage_to_laserscan_node \
  --ros-args \
  -r depth:=/zed/depth/image \
  -r depth_camera_info:=/zed/rgb/camera_info \
  -r scan:=/scan \
  -p output_frame:=camera_link \
  -p range_min:=0.3 -p range_max:=3.0 -p scan_height:=30 -p scan_time:=0.033 \
  >$LOG/d2s.log 2>&1 &
sleep 2

echo ">>> [5/6] slam_toolbox (2D SLAM, mapping) -> /map + map->odom"
# MUST launch via the official launch file: async_slam_toolbox_node is a LifecycleNode — a plain
# `ros2 run` leaves it UNCONFIGURED (no params declared, no /scan subscription, no map). The launch
# file autostarts it (configure -> activate). use_sim_time:=false (we run on wall clock).
ros2 launch slam_toolbox online_async_launch.py \
  slam_params_file:=$V2/slam_toolbox_2d.yaml use_sim_time:=false >$LOG/slam.log 2>&1 &
sleep 6
if pgrep -f async_slam_toolbox_node >/dev/null; then echo "    slam_toolbox up (lifecycle active)."; else echo "    !! slam_toolbox died — see $LOG/slam.log"; cat $LOG/slam.log; fi

if [ $DO_RVIZ -eq 1 ]; then
  echo ">>> [6/6] RViz (watch /map + /scan)"
  rviz2 -d /opt/ros/jazzy/share/slam_toolbox/config/slam_toolbox_default.rviz >$LOG/rviz.log 2>&1 & sleep 3
fi

echo
echo "==================================================================="
echo " DRIVE MANUALLY (WASD) to build the map. Keys (this terminal):"
echo "   w/s = fwd/back   a/d = strafe L/R   q/e = rotate   space/k = stop"
echo "   z/x = slower/faster   (start SLOW, depth is ~1 FPS!)"
echo " Watch /map grow in RViz. Drive the perimeter ~1.5-2 m from walls."
echo
echo " To SAVE the map when happy (run in ANOTHER terminal):"
echo "   source /opt/ros/jazzy/setup.bash && export ROS_DOMAIN_ID=0"
echo "   ros2 run nav2_map_server map_saver_cli -f $R/utils/scan_map --ros-args -p save_map_timeout:=10.0"
echo
echo " Ctrl-C here = stop + clean up everything."
echo "==================================================================="
# WASD teleop in the FOREGROUND (owns the terminal for key input). Runs under conda because
# system python3 lacks PyYAML that rclpy needs; conda has rclpy (via PYTHONPATH) + yaml.
bash -c "$CONDA && python3 $V2/wasd_teleop.py --speed 0.12 --turn 0.5"

echo ">>> teleop exited."; $CLEAN
