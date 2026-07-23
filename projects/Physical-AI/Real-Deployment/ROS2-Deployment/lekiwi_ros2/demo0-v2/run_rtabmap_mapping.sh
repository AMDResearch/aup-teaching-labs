#!/bin/bash
# ============ DEMO 0-v2: RTAB-Map LIVE MAPPING (for autonomous exploration) ============
# Builds the map ONLINE from live /zed RGB-D + wheel /odom, and publishes a growing
# occupancy grid on /map (absolute) so Nav2's global costmap + the frontier explorer
# can navigate into the unknown while mapping.
#
# NOTE: this online map is throwaway — the final "beautiful" map still comes from the
# offline demo0/build_map.sh (iters=32) run on the recorded session afterwards.
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=0
export DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000
export XAUTHORITY=$(ls /run/user/1000/.mutter-Xwaylandauth.* 2>/dev/null | head -1)

# ZED mount on LeKiwi: ~0.14 m forward of base center (plate edge 0.125 + ZED body), 0.11 m up,
# facing forward, no tilt.
ros2 run tf2_ros static_transform_publisher \
  --x 0.14 --y 0.0 --z 0.11 --roll -1.5708 --pitch 0.0 --yaw -1.5708 \
  --frame-id base_link --child-frame-id camera_optical_frame &

# Mapping mode (localization:=false). map_topic:=/map -> grid published on absolute /map.
# DetectionRate 2 refreshes the map/obstacle cloud reasonably fast. Default (normals) ground
# segmentation is used: forcing height-based segmentation marked the floor as obstacles and
# boxed the robot in. The short ~5cm colour blocks therefore can't be reliably avoided live
# (VGA depth is too sparse on them) -> keep blocks out of the driving lanes physically.
#
# Grid/RangeMax 2.5  : ignore FAR depth (noisiest) so distant corners aren't falsely marked
#                      occupied and blocked before the robot gets close enough to see clearly.
# Grid/RayTracing on : actively clear free space between sensor and obstacles, so a false
#                      obstacle gets ERASED when it's later re-observed as free (un-sticks corners).
exec ros2 launch rtabmap_launch rtabmap.launch.py \
  localization:=false \
  map_topic:=/map \
  database_path:=$HOME/.ros/rtabmap.db \
  rgb_topic:=/zed/rgb/image \
  depth_topic:=/zed/depth/image \
  camera_info_topic:=/zed/rgb/camera_info \
  frame_id:=base_link \
  odom_topic:=/odom \
  visual_odometry:=false \
  approx_sync:=true \
  approx_sync_max_interval:=0.1 \
  qos:=1 \
  rtabmap_viz:=false \
  rviz:=false \
  rtabmap_args:="--delete_db_on_start --Rtabmap/DetectionRate 2 --RGBD/LinearUpdate 0.05 --RGBD/AngularUpdate 0.05 --Grid/RangeMax 2.5 --Grid/RayTracing true"
