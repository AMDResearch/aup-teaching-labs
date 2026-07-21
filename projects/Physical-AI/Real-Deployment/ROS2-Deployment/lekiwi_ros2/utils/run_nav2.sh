#!/bin/bash
RD_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"  # repo-relative root
# Nav2 core (holonomic MPPI), minimal launch (no collision_monitor/smoother).
# Map+localization from RTAB-Map (run_localize.sh); /cmd_vel -> lekiwi_base_ros.py.
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=0
exec ros2 launch $RD_ROOT/utils/nav2_min.launch.py
