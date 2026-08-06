"""Nav2 for demo0-v2 SGBM 2D autonomous exploration. Same nodes as nav2_explore.launch.py
but with demo0-v2/nav2_explore_2d.yaml — costmaps consume /scan (depthimage_to_laserscan) +
/map (slam_toolbox). No map_server (slam_toolbox publishes the live /map)."""
import os as _os  # repo-relative root (works from a git clone)
_ROS2 = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os
from launch import LaunchDescription
from launch_ros.actions import Node

P = os.path.join(_ROS2, "demo0-v2/nav2_explore_2d.yaml")


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
