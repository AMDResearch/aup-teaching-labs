#!/usr/bin/env python3
"""Navigate to colored block(s) by name. Snaps the goal to the nearest reachable free
cell near the block (so blocks touching obstacles are still reachable) and faces the block.
'from A to B' = pass multiple colors: they are visited in order.

Run:
  source /opt/ros/jazzy/setup.bash
  python3 send_block_goal.py green red      # go to green, then red
"""
import os as _os  # repo-relative root (works from a git clone)
_ROS2 = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os, sys, math, numpy as np, cv2, yaml
import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from nav2_msgs.action import NavigateToPose
from geometry_msgs.msg import PoseStamped

WP  = os.path.join(_ROS2, "utils/block_waypoints.txt")
MAPY = os.path.join(_ROS2, "utils/scene_map.yaml")
CLEAR_CELLS = 3          # require this radius of non-occupied cells (robot fits)
STANDOFF = 0.10          # default extra stop-back distance from block (m); override with --standoff

def load_waypoints():
    d = {}
    for ln in open(WP):
        p = ln.split()
        if len(p) == 3: d[p[0]] = (float(p[1]), float(p[2]))
    return d

def load_map():
    m = yaml.safe_load(open(MAPY))
    img = cv2.imread(os.path.join(os.path.dirname(MAPY), m["image"]), cv2.IMREAD_GRAYSCALE)
    return img, m["resolution"], m["origin"][0], m["origin"][1]

def snap_to_free(bx, by, img, res, ox, oy):
    """Return (gx,gy) grid cell nearest to block that is free with clearance."""
    H, W = img.shape
    gx0 = int((bx - ox) / res); gy0 = int((by - oy) / res)
    def clear(gx, gy):
        if not (CLEAR_CELLS <= gx < W-CLEAR_CELLS and CLEAR_CELLS <= gy < H-CLEAR_CELLS): return False
        win = img[H-1-(gy+CLEAR_CELLS):H-1-(gy-CLEAR_CELLS)+1, gx-CLEAR_CELLS:gx+CLEAR_CELLS+1]
        return (img[H-1-gy, gx] == 254) and (win != 0).all()   # free center, no occupied nearby
    if clear(gx0, gy0): return gx0, gy0
    for r in range(1, max(H, W)):
        best = None; bestd = 1e9
        for dx in range(-r, r+1):
            for dy in range(-r, r+1):
                if max(abs(dx), abs(dy)) != r: continue
                gx, gy = gx0+dx, gy0+dy
                if clear(gx, gy):
                    d = dx*dx+dy*dy
                    if d < bestd: bestd = d; best = (gx, gy)
        if best: return best
    return gx0, gy0

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("colors", nargs="+", help="red/purple/blue/green (order = visit order)")
    ap.add_argument("--standoff", type=float, default=STANDOFF,
                    help="extra distance to stop back from the block, metres (default %(default)s)")
    a = ap.parse_args()
    colors = a.colors; standoff = a.standoff
    wps = load_waypoints(); img, res, ox, oy = load_map()
    for c in colors:
        if c not in wps: print(f"unknown color '{c}', have {list(wps)}"); return
    rclpy.init(); node = Node("send_block_goal")
    ac = ActionClient(node, NavigateToPose, "/navigate_to_pose")
    print("waiting for Nav2 action server..."); ac.wait_for_server()
    for c in colors:
        bx, by = wps[c]
        gx, gy = snap_to_free(bx, by, img, res, ox, oy)
        x = ox + (gx+0.5)*res; y = oy + (gy+0.5)*res
        # back off a bit farther from the block (stop ~standoff m away, not on top of it)
        dx, dy = x - bx, y - by; nrm = math.hypot(dx, dy)
        if nrm > 1e-3: x += standoff*dx/nrm; y += standoff*dy/nrm
        yaw = math.atan2(by - y, bx - x)                       # face the block
        ps = PoseStamped(); ps.header.frame_id = "map"; ps.header.stamp = node.get_clock().now().to_msg()
        ps.pose.position.x = x; ps.pose.position.y = y
        ps.pose.orientation.z = math.sin(yaw/2); ps.pose.orientation.w = math.cos(yaw/2)
        goal = NavigateToPose.Goal(); goal.pose = ps
        print(f"\n>>> {c} block at ({bx:+.2f},{by:+.2f}) -> goal ({x:+.2f},{y:+.2f}) facing {math.degrees(yaw):.0f}°")
        fut = ac.send_goal_async(goal); rclpy.spin_until_future_complete(node, fut)
        gh = fut.result()
        if not gh.accepted: print("  goal REJECTED"); continue
        rf = gh.get_result_async(); rclpy.spin_until_future_complete(node, rf)
        st = rf.result().status
        print(f"  {'ARRIVED ✓' if st == 4 else f'ended status={st}'}")
    node.destroy_node(); rclpy.shutdown()

if __name__ == "__main__": main()
