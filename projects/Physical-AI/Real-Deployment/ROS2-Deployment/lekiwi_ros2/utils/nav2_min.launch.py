import os as _os  # repo-relative root (works from a git clone)
_ROS2 = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os
from launch import LaunchDescription
from launch_ros.actions import Node

P = os.path.join(_ROS2, "utils/nav2_lekiwi.yaml")
MAP = os.path.join(_ROS2, "utils/scene_map.yaml")

def generate_launch_description():
    common = dict(output="screen", parameters=[P])
    return LaunchDescription([
        Node(package="nav2_map_server", executable="map_server", name="map_server", output="screen",
             parameters=[{"yaml_filename": MAP, "topic_name": "map", "frame_id": "map"}]),
        Node(package="nav2_controller", executable="controller_server", name="controller_server", **common),
        Node(package="nav2_planner", executable="planner_server", name="planner_server", **common),
        Node(package="nav2_behaviors", executable="behavior_server", name="behavior_server", **common),
        Node(package="nav2_bt_navigator", executable="bt_navigator", name="bt_navigator", **common),
        Node(package="nav2_lifecycle_manager", executable="lifecycle_manager",
             name="lifecycle_manager_navigation", output="screen",
             parameters=[{"autostart": True,
                          "node_names": ["map_server", "controller_server", "planner_server",
                                         "behavior_server", "bt_navigator"]}]),
    ])
