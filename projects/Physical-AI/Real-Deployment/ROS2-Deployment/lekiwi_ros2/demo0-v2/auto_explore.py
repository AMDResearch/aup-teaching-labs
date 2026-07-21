#!/usr/bin/env python3
"""demo0-v2: autonomous frontier exploration for LeKiwi.

Reads the live occupancy grid RTAB-Map publishes on /map (mapping mode), finds the
boundary between known-free and unknown space (frontiers), clusters them, and repeatedly
sends the nearest frontier as a Nav2 goal — driving the robot to explore on its own.
When no frontier is left it returns to the origin (0,0,0) and stops.

The map here is only used to DECIDE WHERE TO GO. The final "beautiful" map still comes
from the offline demo0/build_map.sh (iters=32) on the session recorded during this run.

Run (conda lerobot-new, after base + ZED + RTAB-Map mapping + nav2_explore are up):
  source /opt/ros/jazzy/setup.bash; conda activate lerobot-new
  export ROS_DOMAIN_ID=0 PYTHONNOUSERSITE=1
  export PYTHONPATH=/opt/ros/jazzy/lib/python3.12/site-packages:$PYTHONPATH
  python demo0-v2/auto_explore.py --scan-spin
"""
import math, time, argparse, threading
import numpy as np
import cv2

import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSDurabilityPolicy, QoSHistoryPolicy
from nav_msgs.msg import OccupancyGrid
from geometry_msgs.msg import PoseStamped, Twist
from tf2_ros import Buffer, TransformListener
from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult


def yaw_to_quat(yaw):
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


class MapWatch(Node):
    """Holds the latest /map and the robot's map-frame pose (via TF). Spun in a background thread."""

    def __init__(self, map_topic, costmap_topic):
        super().__init__("auto_explore_mapwatch")
        self.grid = None
        self.costmap = None
        # RTAB-Map publishes the grid latched (transient_local), like the nav2 static layer expects.
        qos = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(OccupancyGrid, map_topic, self._on_map, qos)
        self.create_subscription(OccupancyGrid, costmap_topic, self._on_costmap, qos)
        self.cmd_pub = self.create_publisher(Twist, "/cmd_vel", 10)  # direct in-place scan rotation
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

    def _on_map(self, msg):
        self.grid = msg

    def _on_costmap(self, msg):
        self.costmap = msg

    def cost_at(self, x, y):
        """Nav2 global-costmap cost (0-100, -1 unknown) at a map-frame point, or None if unknown/off-grid.
        Used to reject frontier goals that sit in inflated/lethal cells (robot would jam at the wall)."""
        cm = self.costmap
        if cm is None:
            return None
        info = cm.info
        cx = int((x - info.origin.position.x) / info.resolution)
        cy = int((y - info.origin.position.y) / info.resolution)
        if 0 <= cx < info.width and 0 <= cy < info.height:
            return cm.data[cy * info.width + cx]
        return None

    def robot_pose(self):
        try:
            t = self.tf_buffer.lookup_transform("map", "base_link", rclpy.time.Time())
            q = t.transform.rotation
            yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                             1.0 - 2.0 * (q.y * q.y + q.z * q.z))
            return t.transform.translation.x, t.transform.translation.y, yaw
        except Exception:
            return None


def find_frontiers(grid, min_cells, robot_r_cells, noise_min=4):
    """Return list of frontier clusters as dicts {x, y, size} in the map frame.

    Frontier = free cell (0) adjacent to unknown (-1), not hugging an obstacle.

    Before extraction the grid is DENOISED (idea borrowed from frontier_exploration_ros2):
    isolated occupied specks from noisy VGA-stereo depth are dropped so they don't create
    phantom obstacles (which block corners) or shatter frontiers into tiny bits.
    """
    info = grid.info
    W, H = info.width, info.height
    res = info.resolution
    ox, oy = info.origin.position.x, info.origin.position.y
    data = np.array(grid.data, dtype=np.int16).reshape(H, W)

    free = (data == 0).astype(np.uint8)
    unknown = (data < 0).astype(np.uint8)
    occ = (data >= 65).astype(np.uint8)

    # --- denoise: drop isolated occupied specks (depth noise) below noise_min cells ---
    if noise_min > 1:
        no, lab_o, stats_o, _ = cv2.connectedComponentsWithStats(occ, 8)
        clean = np.zeros_like(occ)
        for i in range(1, no):
            if stats_o[i, cv2.CC_STAT_AREA] >= noise_min:
                clean[lab_o == i] = 1
        occ = clean

    k3 = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    unknown_adj = cv2.dilate(unknown, k3)
    # keep frontiers a robot-radius away from known obstacles so goals stay reachable
    occ_dil = cv2.dilate(occ, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * robot_r_cells + 1,) * 2))
    frontier = (free & unknown_adj & (occ_dil == 0)).astype(np.uint8)
    # close small gaps so a doorway frontier broken by noise stays one cluster (passes min size)
    frontier = cv2.morphologyEx(frontier, cv2.MORPH_CLOSE, k3)

    n, lab, stats, cent = cv2.connectedComponentsWithStats(frontier, 8)
    clusters = []
    for i in range(1, n):
        size = int(stats[i, cv2.CC_STAT_AREA])
        if size < min_cells:
            continue
        # pick the frontier cell nearest the centroid -> a real free goal cell
        cy_, cx_ = cent[i][1], cent[i][0]
        ys, xs = np.where(lab == i)
        j = np.argmin((xs - cx_) ** 2 + (ys - cy_) ** 2)
        gx = ox + (float(xs[j]) + 0.5) * res
        gy = oy + (float(ys[j]) + 0.5) * res
        clusters.append({"x": gx, "y": gy, "size": size})
    return clusters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map-topic", default="/map", help="occupancy grid from RTAB-Map mapping")
    ap.add_argument("--costmap-topic", default="/global_costmap/costmap",
                    help="Nav2 global costmap, used to reject unreachable (wall-hugging) frontier goals")
    ap.add_argument("--cost-threshold", type=int, default=50,
                    help="reject frontier goals whose global-costmap cost >= this (inflated/lethal)")
    ap.add_argument("--min-frontier", type=int, default=8, help="min frontier cluster size (cells)")
    ap.add_argument("--pick", choices=["best", "nearest", "largest"], default="best",
                    help="best = size/(1+dist): commit to big openings (doorways) yet prefer near")
    ap.add_argument("--goal-timeout", type=float, default=60.0, help="per-goal timeout (s)")
    ap.add_argument("--blacklist-radius", type=float, default=0.4, help="don't retry near a visited/failed goal (m)")
    ap.add_argument("--min-goal-dist", type=float, default=0.15,
                    help="ignore frontiers closer than this to the robot (m) — avoids looping at your feet")
    ap.add_argument("--goal-standoff", type=float, default=0.35,
                    help="stop this far SHORT of the frontier boundary (m) — goal lands in safe known-free "
                         "space, not hugging the wall; the robot still sees the unknown from there")
    ap.add_argument("--robot-radius", type=float, default=0.16, help="clearance from obstacles for goals (m); "
                    "matches nav2_lekiwi.yaml footprint (true body radius ~0.125 m + margin)")
    ap.add_argument("--scan-spin", action="store_true", help="sweep in place at each waypoint for coverage")
    ap.add_argument("--scan-sweep", type=float, default=2.5,
                    help="half-sweep angle (rad): scan turns LEFT +this then RIGHT -this then back. "
                         "2.5=±143deg (covers both sides well). Up to ~3.0 for near-full view. 0 disables")
    ap.add_argument("--scan-speed", type=float, default=0.3,
                    help="in-place scan rotation speed (rad/s); slower = sharper frames / cleaner map")
    ap.add_argument("--noise-min", type=int, default=4,
                    help="drop occupied specks smaller than this many cells (VGA depth denoise)")
    ap.add_argument("--no-progress-timeout", type=float, default=15.0,
                    help="cancel a goal if distance_remaining stops improving for this long (s)")
    ap.add_argument("--progress-eps", type=float, default=0.05, help="min distance improvement to count as progress (m)")
    ap.add_argument("--patience", type=int, default=3, help="empty-frontier scans before declaring done")
    ap.add_argument("--max-goals", type=int, default=60, help="safety cap on number of goals")
    ap.add_argument("--time-budget", type=float, default=1800.0, help="safety cap on total run time (s)")
    ap.add_argument("--no-return-home", action="store_true", help="stop where finished instead of (0,0)")
    ap.add_argument("--latest-tf", action="store_true",
                    help="stamp goal poses with time 0 so Nav2 uses the LATEST map->odom instead of "
                         "requesting 'now' (avoids 'extrapolation into future' aborts when the SLAM "
                         "map->odom lags, e.g. the SGBM 2D pipeline). OFF by default (RTAB unchanged).")
    args = ap.parse_args()

    rclpy.init()
    watch = MapWatch(args.map_topic, args.costmap_topic)
    exe = MultiThreadedExecutor()
    exe.add_node(watch)
    threading.Thread(target=exe.spin, daemon=True).start()

    nav = BasicNavigator()

    def gstamp():
        # time 0 -> tf uses the latest available transform (no future-extrapolation); else "now"
        return rclpy.time.Time().to_msg() if args.latest_tf else nav.get_clock().now().to_msg()

    def goto(x, y, yaw, timeout, what):
        p = PoseStamped()
        p.header.frame_id = "map"
        p.header.stamp = gstamp()
        p.pose.position.x = float(x)
        p.pose.position.y = float(y)
        qx, qy, qz, qw = yaw_to_quat(yaw)
        p.pose.orientation.z, p.pose.orientation.w = qz, qw
        nav.goToPose(p)
        t0 = time.time()
        best_d = None          # smallest distance_remaining seen so far
        t_prog = time.time()   # last time we made real progress
        while not nav.isTaskComplete():
            now = time.time()
            if now - t0 > timeout:
                nav.cancelTask()
                print(f"  [timeout] {what} after {timeout:.0f}s")
                time.sleep(0.5)
                return False
            # no-progress watchdog (borrowed from frontier_exploration_ros2): if the robot
            # isn't getting closer to the goal, it's stuck -> give up early instead of grinding.
            fb = nav.getFeedback()
            if fb is not None:
                d = float(fb.distance_remaining)
                if d > 0.0 and (best_d is None or d < best_d - args.progress_eps):
                    best_d = d
                    t_prog = now
                elif best_d is not None and now - t_prog > args.no_progress_timeout:
                    nav.cancelTask()
                    print(f"  [stuck] {what}: no progress {args.no_progress_timeout:.0f}s (d={d:.2f}m)")
                    time.sleep(0.5)
                    return False
            time.sleep(0.3)
        ok = nav.getResult() == TaskResult.SUCCEEDED
        print(f"  [{'ok' if ok else 'fail'}] {what}")
        return ok

    def plannable(sx, sy, gx, gy):
        """Ask Nav2's planner if a path to (gx,gy) exists. Frontiers on wall boundaries have
        no valid path (planner returns nothing) -> we skip them instead of driving into the wall."""
        def ps(x, y):
            p = PoseStamped()
            p.header.frame_id = "map"
            p.header.stamp = gstamp()
            p.pose.position.x = float(x)
            p.pose.position.y = float(y)
            p.pose.orientation.w = 1.0
            return p
        try:
            path = nav.getPath(ps(sx, sy), ps(gx, gy))
        except Exception:
            return False
        return path is not None and len(path.poses) > 1

    def _rotate(delta):
        # Rotate in place by delta (rad) via DIRECT /cmd_vel — NOT Nav2's spin behavior, which
        # jams on the (spin-poisoned) costmap. We only ever call this at safe stand-off points.
        if abs(delta) < 0.02:
            return
        tw = Twist()
        tw.angular.z = (1.0 if delta > 0 else -1.0) * args.scan_speed
        dur = abs(delta) / args.scan_speed
        t0 = time.time()
        while time.time() - t0 < dur:
            watch.cmd_pub.publish(tw)      # 20 Hz keeps the base watchdog alive
            time.sleep(0.05)
        watch.cmd_pub.publish(Twist())     # stop
        time.sleep(0.4)

    def _clear_costmaps():
        # wipe the false obstacles the in-place rotation sprayed into the local costmap,
        # so the next navigation starts clean and doesn't get jammed.
        try:
            nav.clearLocalCostmap()
        except Exception:
            pass

    def scan(sweep, label="scan"):
        # LEFT then RIGHT then back to centre. Net rotation 0 (cable-safe), covers both sides:
        # +sweep = turn left, -2*sweep = swing right past centre to -sweep, +sweep = back to 0.
        if not (args.scan_spin and sweep > 0.01):
            return
        deg = math.degrees(sweep)
        print(f"  {label}: look LEFT +{deg:.0f}° then RIGHT -{deg:.0f}° (net-zero, {args.scan_speed:.2f} rad/s)...")
        for d in (sweep, -2.0 * sweep, sweep):
            _rotate(d)
        _clear_costmaps()

    def full_look(label="initial look-around"):
        # one-time 360° look (see the wall BEHIND the start too, so return-home won't
        # reverse into it). Net rotation 0: +180 back to 0, then -180 back to 0.
        if not args.scan_spin:
            return
        print(f"  {label} (full 360°, net-zero, {args.scan_speed:.2f} rad/s)...")
        for d in (math.pi, -math.pi, -math.pi, math.pi):
            _rotate(d)
        _clear_costmaps()

    # wait for the map + a valid robot pose (mapping + TF up)
    print(">>> waiting for /map and robot pose ...")
    while rclpy.ok():
        pose = watch.robot_pose()
        if watch.grid is not None and pose is not None:
            break
        time.sleep(0.5)
    print(f">>> mapping live. robot at ({pose[0]:+.2f}, {pose[1]:+.2f}).")

    res = watch.grid.info.resolution
    robot_r_cells = max(1, int(round(args.robot_radius / res)))
    blacklist = []
    empty_scans = 0
    goals_done = 0
    t_start = time.time()

    try:
        # look all around the START first: maps the start surroundings (incl. the wall
        # behind) before driving off, so frontiers are sensible and return-home is safe.
        full_look()
        print(">>> exploring...")
        while rclpy.ok():
            if goals_done >= args.max_goals:
                print(f">>> reached max-goals ({args.max_goals}); finishing.")
                break
            if time.time() - t_start > args.time_budget:
                print(">>> time budget exhausted; finishing.")
                break

            pose = watch.robot_pose()
            if pose is None:
                time.sleep(0.5)
                continue
            rx, ry, ryaw = pose
            raw = find_frontiers(watch.grid, args.min_frontier, robot_r_cells, args.noise_min)

            def reachable(c):  # reject goals sitting in an inflated/lethal costmap cell (wall-hugging)
                cost = watch.cost_at(c["x"], c["y"])
                return cost is None or cost < 0 or cost < args.cost_threshold

            raw = [c for c in raw if reachable(c)]
            not_blacklisted = [c for c in raw
                               if all(math.hypot(c["x"] - bx, c["y"] - by) > args.blacklist_radius
                                      for bx, by in blacklist)]
            # normal: also require the frontier to be a real step away (not at our feet)
            clusters = [c for c in not_blacklisted
                        if math.hypot(c["x"] - rx, c["y"] - ry) >= args.min_goal_dist]

            if not clusters:
                # escape (borrowed from frontier_exploration_ros2): if the only thing left is
                # a close frontier, relax the near-goal gate rather than stall. Blacklist-on-
                # success stops it looping there.
                if not_blacklisted:
                    clusters = not_blacklisted
                    print(f">>> escape: relaxing near-goal gate ({len(clusters)} frontier(s) left)")
                else:
                    empty_scans += 1
                    print(f">>> no frontier ({empty_scans}/{args.patience}) — "
                          f"{len(raw)} raw, all visited/blacklisted. waiting for map to grow...")
                    if empty_scans >= args.patience:
                        break
                    time.sleep(2.0)
                    continue
            empty_scans = 0

            def score(c):
                dist = math.hypot(c["x"] - rx, c["y"] - ry)
                if args.pick == "largest":
                    return c["size"]
                if args.pick == "nearest":
                    return -dist
                return c["size"] / (1.0 + dist)     # "best"

            def standoff_goal(c):
                # stand off SHORT of the frontier boundary, toward the robot (known-free side),
                # so the goal sits in safe white space, not against the wall.
                d = math.hypot(rx - c["x"], ry - c["y"])
                if d < 1e-3:
                    return c["x"], c["y"]
                so = min(args.goal_standoff, max(0.0, d - 0.1))
                return c["x"] + (rx - c["x"]) / d * so, c["y"] + (ry - c["y"]) / d * so

            # Try candidates best-first, but only GO to a stand-off point the planner can reach.
            # Wall-boundary goals (no path / in inflation) are skipped + blacklisted, not driven into.
            tgt = None
            gx = gy = 0.0
            for c in sorted(clusters, key=score, reverse=True):
                sgx, sgy = standoff_goal(c)
                if plannable(rx, ry, sgx, sgy):
                    tgt = c
                    gx, gy = sgx, sgy
                    break
                blacklist.append((c["x"], c["y"]))
                print(f"  skip unreachable frontier ({c['x']:+.2f}, {c['y']:+.2f}) — no safe path")
            if tgt is None:
                empty_scans += 1
                print(f">>> no REACHABLE frontier ({empty_scans}/{args.patience}) — "
                      f"{len(clusters)} candidate(s), none safely plannable (walls/enclosed). "
                      f"map may already be complete.")
                if empty_scans >= args.patience:
                    break
                time.sleep(2.0)
                continue
            empty_scans = 0

            # Face the frontier (turn coupled with driving there, not a standalone spin) so the
            # camera maps the unknown ahead; but STOP at the safe stand-off point (gx,gy).
            yaw = math.atan2(tgt["y"] - ry, tgt["x"] - rx)
            goals_done += 1
            print(f">>> goal {goals_done}: frontier ({tgt['x']:+.2f}, {tgt['y']:+.2f}) "
                  f"via safe point ({gx:+.2f}, {gy:+.2f}) size={tgt['size']} "
                  f"[{len(clusters)} reachable of {len(raw)} raw]")
            ok = goto(gx, gy, yaw, args.goal_timeout, "reach frontier")
            # mark this frontier region done whether we reached it or not, so we never loop
            # back to the same spot. New ground opened up nearby will spawn frontiers
            # farther out (beyond blacklist_radius), which still get explored.
            blacklist.append((tgt["x"], tgt["y"]))
            if not ok:
                continue
            scan(args.scan_sweep, "scanning waypoint")

        print(">>> exploration complete.")
        if not args.no_return_home:
            print(">>> returning to origin (0, 0)...")
            goto(0.0, 0.0, 0.0, max(args.goal_timeout, 90.0), "return home")
            # restore the ORIGINAL start heading too (goal yaw tolerance is loose, so the
            # drive-home leaves any heading). One small spin -> full initial pose (0,0,0).
            pose = watch.robot_pose()
            if pose is not None:
                d = math.atan2(math.sin(-pose[2]), math.cos(-pose[2]))  # shortest turn to yaw 0
                if abs(d) > 0.1:
                    print(f"  aligning to start heading ({math.degrees(d):+.0f}°)...")
                    _rotate(d)
            print(">>> back at start pose (0, 0, 0).")
    except KeyboardInterrupt:
        print(">>> interrupted; cancelling current goal.")
        nav.cancelTask()
    finally:
        try:
            watch.destroy_node()
        except Exception:
            pass
        rclpy.shutdown()
        print(">>> auto_explore done.")


if __name__ == "__main__":
    main()
