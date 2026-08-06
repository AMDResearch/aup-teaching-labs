#!/bin/bash
RD_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"  # repo-relative root
# ============ DEMO 2: navigate to colored blocks by name ============
# Usage:  bash demo2_color_nav.sh red          # go to red block
#         bash demo2_color_nav.sh green red     # green then red
# Auto-cleans everything on start AND exit. Put the robot at recording START first.
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=0
export DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000
export XAUTHORITY=$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1)
CONDA='source ~/miniconda3/etc/profile.d/conda.sh && conda activate lerobot-new && export PYTHONNOUSERSITE=1 && export PYTHONPATH=/opt/ros/jazzy/lib/python3.12/site-packages:$PYTHONPATH'
CLEAN="bash $RD_ROOT/utils/lekiwi_cleanup.sh"
LOG=/tmp/lekiwi_demo; mkdir -p $LOG
COLORS="${@:-red}"

trap 'echo; echo ">>> stopping + cleaning up..."; $CLEAN; echo "done."; exit 0' EXIT INT TERM

[ -f $RD_ROOT/utils/block_waypoints.txt ] || { echo "!! block_waypoints.txt missing — run block_localize.py first"; exit 1; }
echo ">>> route: $COLORS"; echo ">>> pre-clean (start from 0)"; $CLEAN; sleep 1
ZED_DEV=$(v4l2-ctl --list-devices 2>/dev/null | grep -A1 "ZED" | grep -oE "video[0-9]+" | head -1 | tr -d 'video'); ZED_DEV=${ZED_DEV:-0}
echo ">>> ZED on /dev/video$ZED_DEV"

echo ">>> [1/5] base driver";      bash -c "$CONDA && python $RD_ROOT/utils/lekiwi_base_ros.py" >$LOG/base.log 2>&1 &
echo ">>> [2/5] ZED live (warmup)"; bash -c "$CONDA && python $RD_ROOT/utils/zed_rgbd_ros2.py --dev $ZED_DEV --iters 10" >$LOG/zed.log 2>&1 &
until grep -q streaming $LOG/zed.log 2>/dev/null; do sleep 2; done; echo "    ZED streaming."
echo ">>> [3/5] RTAB-Map localization"; bash -c "bash $RD_ROOT/utils/run_localize.sh" >$LOG/loc.log 2>&1 & sleep 8
echo ">>> init pose at origin (robot must be at start!)"
ros2 topic pub -1 /rtabmap/initialpose geometry_msgs/msg/PoseWithCovarianceStamped "{header: {frame_id: map}, pose: {pose: {orientation: {w: 1.0}}}}" >/dev/null 2>&1
echo ">>> [4/5] Nav2"; bash -c "bash $RD_ROOT/utils/run_nav2.sh" >$LOG/nav2.log 2>&1 &
until grep -q "Managed nodes are active" $LOG/nav2.log 2>/dev/null; do sleep 1; done; echo "    Nav2 active."
echo ">>> [5/5] RViz"; rviz2 -d /opt/ros/jazzy/share/nav2_bringup/rviz/nav2_default_view.rviz >$LOG/rviz.log 2>&1 & sleep 3

echo; echo "=========== navigating colour route: $COLORS ==========="
python3 -u $RD_ROOT/demo2/send_block_goal.py $COLORS
echo; echo ">>> route done. Ctrl-C to stop + auto-clean.  (or run: python3 $RD_ROOT/demo2/send_block_goal.py <color>)"
wait
