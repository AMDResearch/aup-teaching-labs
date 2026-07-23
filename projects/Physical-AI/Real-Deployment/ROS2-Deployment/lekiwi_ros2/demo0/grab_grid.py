#!/usr/bin/env python3
"""Stage D (ray-tracing): capture RTAB-Map's ray-traced 2D occupancy grid (/map) and save it
as utils/scene_map.pgm/.yaml — REPLACES the old point-count make_grid.py.

Why ray-tracing beats point-counting: RTAB-Map clears every cell a camera ray passes THROUGH
as free and only marks the cell a ray HITS as occupied. That (a) erases isolated depth-noise
specks that the old count>=threshold logic left as phantom obstacles, and (b) produces a
continuous obstacle shell instead of detached lumps. See run_rtabmap_wheelodom.sh for the
Grid/RayTracing + Grid/3D=false params that make /map a 2D ray-traced grid.

Runs CONCURRENTLY with the RTAB-Map replay (build_map.sh) so it catches the grid while the
node is publishing. Keeps the latest /map; saves on SIGINT (build_map sends it after replay)
or after --max seconds as a fallback. Uses the same QoS as auto_explore.py (known to work;
nav2 map_saver's lifecycle/QoS handshake was unreliable here).

Optional --odom: overlay the driven corridor as guaranteed-free (ported from make_grid.py:
the robot physically drove there, so it's traversable even if a ray briefly grazed it).
"""
import os as _os  # repo-relative root (works from a git clone)
_ROS2 = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os, argparse, signal, numpy as np, cv2, yaml
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSDurabilityPolicy, QoSHistoryPolicy
from nav_msgs.msg import OccupancyGrid

ROBOT_R_CELLS = 6          # ~0.30 m free-corridor half-width (matches old make_grid.py)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="/map")
    ap.add_argument("--out", default=os.path.join(_ROS2, "utils/scene_map"))
    ap.add_argument("--odom", default=None, help="odom.txt: mark driven corridor as free")
    ap.add_argument("--max", type=float, default=300.0, help="fallback auto-save timeout (s)")
    args = ap.parse_args()

    class Grab(Node):
        def __init__(self):
            super().__init__("grab_grid")
            qos = QoSProfile(reliability=QoSReliabilityPolicy.RELIABLE,
                             durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
                             history=QoSHistoryPolicy.KEEP_LAST, depth=1)
            self.msg = None; self.n = 0
            self.create_subscription(OccupancyGrid, args.topic, self._cb, qos)
            self.create_timer(args.max, self.save_and_exit)
            self.get_logger().info(f"subscribed {args.topic} (save on SIGINT or {args.max:.0f}s)")

        def _cb(self, m):
            self.msg = m; self.n += 1

        def save_and_exit(self):
            if self.msg is None:
                self.get_logger().error("no /map received — nothing saved"); rclpy.shutdown(); return
            m = self.msg; W, H, res = m.info.width, m.info.height, m.info.resolution
            ox, oy = m.info.origin.position.x, m.info.origin.position.y
            d = np.array(m.data, dtype=np.int16).reshape(H, W)   # row 0 = origin (bottom), row-major
            grid = np.full((H, W), -1, np.int8)
            grid[d == 0] = 0                                     # free
            grid[d >= 65] = 100                                  # occupied (rtabmap uses 0/100/-1)

            if args.odom and os.path.exists(args.odom):
                px = []; py = []
                for ln in open(args.odom):
                    ln = ln.strip()
                    if not ln or ln.startswith("#"): continue
                    p = ln.split(); px.append(float(p[1])); py.append(float(p[2]))
                traj = np.zeros((H, W), np.uint8)
                for ax, ay in zip(px, py):
                    cx = int((ax - ox) / res); cy = int((ay - oy) / res)
                    if 0 <= cx < W and 0 <= cy < H: traj[cy, cx] = 1
                traj = cv2.dilate(traj, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2*ROBOT_R_CELLS+1,)*2))
                grid[traj > 0] = 0                               # driven corridor -> guaranteed free

            img = np.full((H, W), 205, np.uint8)
            img[grid == 0] = 254; img[grid == 100] = 0
            img = np.flipud(img)                                 # pgm top row = highest y
            cv2.imwrite(args.out + ".pgm", img)
            cv2.imwrite(args.out + "_preview.png",
                        cv2.resize(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), (W*5, H*5), interpolation=cv2.INTER_NEAREST))
            yaml.safe_dump({"image": os.path.basename(args.out) + ".pgm", "resolution": float(res),
                            "origin": [float(ox), float(oy), 0.0], "negate": 0,
                            "occupied_thresh": 0.65, "free_thresh": 0.25},
                           open(args.out + ".yaml", "w"), sort_keys=False)
            free = int((grid == 0).sum()); occ = int((grid == 100).sum()); unk = int((grid == -1).sum())
            self.get_logger().info(f"saved {args.out}.pgm  {W}x{H} origin=({ox:.2f},{oy:.2f})  "
                                   f"free={free} occ={occ} unk={unk}  (from {self.n} msgs)")
            rclpy.shutdown()

    rclpy.init(); n = Grab()
    # NOTE: don't use rclpy.spin() + a signal handler — spin() is a blocking C call that only
    # returns to Python (so the handler can run) when a message arrives. If /map is silent, a
    # SIGINT would be queued forever and the process hangs. Instead spin_once in a short loop and
    # poll a flag the signal sets, so SIGINT is honoured within ~0.2s regardless of traffic.
    stop = {"v": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("v", True))
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("v", True))
    while rclpy.ok() and not stop["v"]:
        rclpy.spin_once(n, timeout_sec=0.2)
    if rclpy.ok():
        n.save_and_exit()


if __name__ == "__main__":
    main()
