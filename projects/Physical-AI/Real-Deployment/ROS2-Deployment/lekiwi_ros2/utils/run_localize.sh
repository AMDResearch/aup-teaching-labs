#!/bin/bash
# RTAB-Map LOCALIZATION mode: load rtabmap.db, provide map->odom + /map (2D grid) + obstacle cloud.
# Uses live /zed RGB-D (from zed_rgbd_ros2.py) + external wheel /odom (from lekiwi_base_ros.py).
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=0
export DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000
export XAUTHORITY=$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1)

# ZED mount on LeKiwi: ~0.14 m forward of base center (plate edge 0.125 + ZED body), 0.11 m up.
ros2 run tf2_ros static_transform_publisher \
  --x 0.14 --y 0.0 --z 0.11 --roll -1.5708 --pitch 0.0 --yaw -1.5708 \
  --frame-id base_link --child-frame-id camera_optical_frame &

exec ros2 launch rtabmap_launch rtabmap.launch.py \
  localization:=true \
  database_path:=$HOME/.ros/rtabmap.db \
  rgb_topic:=/zed/rgb/image \
  depth_topic:=/zed/depth/image \
  camera_info_topic:=/zed/rgb/camera_info \
  frame_id:=base_link \
  odom_topic:=/odom \
  visual_odometry:=false \
  approx_sync:=true approx_sync_max_interval:=0.1 qos:=1 \
  rtabmap_viz:=false rviz:=false
