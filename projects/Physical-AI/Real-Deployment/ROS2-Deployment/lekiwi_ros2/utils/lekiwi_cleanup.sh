#!/bin/bash
# Thoroughly kill ALL LeKiwi/ROS demo processes and stop the motors. Safe to run anytime.
# Robust against: long node names (>15 chars), self-match (excludes this script & grep).
SELF=$$
PATTERNS="lekiwi_base_ros.py|zed_rgbd_ros2.py|zed_sgbd_scan.py|stage_c_replay|send_block_goal.py|\
nav2_explore_2d.launch|online_async_launch|\
nav2_controller/controller_server|nav2_planner/planner_server|nav2_behaviors/behavior_server|\
nav2_bt_navigator/bt_navigator|nav2_map_server/map_server|nav2_lifecycle_manager/lifecycle_manager|\
rtabmap_slam/rtabmap|rtabmap_odom/rgbd_odometry|rtabmap_viz/rtabmap_viz|\
tf2_ros/static_transform_publisher|rviz2|nav2_min.launch|nav2_explore.launch|\
run_localize.sh|run_nav2.sh|run_rtabmap_mapping.sh|auto_explore.py|explore_lite_py.py|\
depthimage_to_laserscan|slam_toolbox|async_slam_toolbox_node|teleop_twist_keyboard"

pids(){ ps -eo pid,args | grep -E "$PATTERNS" | grep -vE "grep|lekiwi_cleanup" | awk -v s=$SELF '$1!=s{print $1}'; }

# 1) polite SIGINT (base driver shutdown stops wheels + releases port)
for p in $(pids); do kill -INT "$p" 2>/dev/null; done
sleep 2
# 2) force-kill survivors
for p in $(pids); do kill -9 "$p" 2>/dev/null; done
sleep 1

# 3) explicit motor stop (port free now)
source ~/miniconda3/etc/profile.d/conda.sh 2>/dev/null && conda activate lerobot-new 2>/dev/null
PYTHONNOUSERSITE=1 python3 - <<'PY' 2>/dev/null
try:
    from lerobot.motors import Motor, MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode
    W={'left_wheel':7,'back_wheel':8,'right_wheel':9}
    b=FeetechMotorsBus(port='/dev/ttyACM0',motors={n:Motor(i,'sts3215',MotorNormMode.RANGE_M100_100) for n,i in W.items()})
    b.connect()
    for n in W: b.write('Operating_Mode',n,OperatingMode.VELOCITY.value)
    b.sync_write('Goal_Velocity',dict.fromkeys(W,0)); b.disable_torque(); b.disconnect()
    print("  motors stopped")
except Exception as e:
    print(f"  (motor stop skipped: {e})")
PY
echo "cleanup done ($(pids | wc -l) demo procs remaining)"
