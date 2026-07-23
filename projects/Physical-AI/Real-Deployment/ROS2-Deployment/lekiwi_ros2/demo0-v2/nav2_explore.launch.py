"""Nav2 for demo0-v2 autonomous exploration — same nodes as nav2_min.launch.py but WITHOUT
map_server. The global costmap's static_layer instead subscribes to the LIVE, growing /map
published by RTAB-Map mapping (run_rtabmap_mapping.sh, map_topic:=/map).

Uses demo0-v2/nav2_explore.yaml (a copy of utils/nav2_lekiwi.yaml with exploration tweaks:
slow speeds, loose final yaw, and a LOW obstacle band so the ~5cm colour blocks are treated
as obstacles and not driven over). demo1/2 keep utils/nav2_lekiwi.yaml untouched.
"""
import os as _os  # repo-relative root (works from a git clone)
_ROS2 = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os
from launch import LaunchDescription
from launch_ros.actions import Node

P = os.path.join(_ROS2, "demo0-v2/nav2_explore.yaml")


def generate_launch_description():
    common = dict(output="screen", parameters=[P])
    return LaunchDescription([
        Node(package="nav2_controller", executable="controller_server", name="controller_server", **common),
        Node(package="nav2_planner", executable="planner_server", name="planner_server", **common),
        Node(package="nav2_behaviors", executable="behavior_server", name="behavior_server", **common),
        Node(package="nav2_bt_navigator", executable="bt_navigator", name="bt_navigator", **common),
        Node(package="nav2_lifecycle_manager", executable="lifecycle_manager",
             name="lifecycle_manager_navigation", output="screen",
             parameters=[{"autostart": True,
                          "node_names": ["controller_server", "planner_server",
                                         "behavior_server", "bt_navigator"]}]),
    ])
