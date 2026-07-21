#!/bin/bash
# Stage C (wheel-odom): RTAB-Map using external wheel odometry (/odom) + robot-mounted ZED.
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=0
export DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000
export XAUTHORITY=$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1)

# ZED mount on LeKiwi: ~0.14 m forward of base center (plate edge 0.125 + ZED body), 0.11 m up,
# facing forward, no tilt.
# base_link -> camera_optical_frame : translation + optical rotation (x-right y-down z-forward)
ros2 run tf2_ros static_transform_publisher \
  --x 0.14 --y 0.0 --z 0.11 --roll -1.5708 --pitch 0.0 --yaw -1.5708 \
  --frame-id base_link --child-frame-id camera_optical_frame &

# Grid/3D=false + Grid/RayTracing=true -> /map is a 2D RAY-TRACED occupancy grid: cells a
# camera ray passes THROUGH are cleared free, only the cell it HITS is occupied. This replaces
# the old point-count make_grid.py (grab_grid.py captures /map instead). RangeMax caps the
# noisiest far depth; MaxObstacleHeight 0.6 matches the old obstacle band top.
exec ros2 launch rtabmap_launch rtabmap.launch.py \
  rgb_topic:=/zed/rgb/image \
  depth_topic:=/zed/depth/image \
  camera_info_topic:=/zed/rgb/camera_info \
  frame_id:=base_link \
  odom_topic:=/odom \
  visual_odometry:=false \
  approx_sync:=true \
  approx_sync_max_interval:=0.05 \
  qos:=1 \
  map_topic:=/map \
  rtabmap_viz:=true \
  rviz:=false \
  rtabmap_args:="--delete_db_on_start --Rtabmap/DetectionRate 2 --RGBD/LinearUpdate 0.05 --RGBD/AngularUpdate 0.05 --Grid/3D false --Grid/RayTracing true --Grid/RangeMax 3.0 --Grid/CellSize 0.05 --Grid/MaxObstacleHeight 0.6"
