#!/usr/bin/env python3
"""demo0-v2: explore_lite-style frontier exploration in PURE PYTHON (no C++ / colcon build).

Mirrors robo-friends/m-explore-ros2 `explore_lite` behaviour, which is what jetbot-ros2 uses:
  * on a timer (planner_frequency) RE-evaluate frontiers from /map,
  * pick the best by explore_lite's cost = potential_scale*distance - gain_scale*size,
  * send it as a Nav2 NavigateToPose goal,
  * on failure, RETRY a few times then BLACKLIST that spot — but KEEP GOING (never "give up"
    after a fixed patience the way auto_explore.py did). This retry-persistence is what tolerates
    the transient map->odom transform aborts that kept stopping auto_explore.py.
Goal poses are stamped time 0 so Nav2 uses the latest TF (no "extrapolation into future").

Reuses the tuned frontier detection + MapWatch from auto_explore.py (same directory).

Run (conda lerobot-new, after base + SGBM scan + slam_toolbox + Nav2 are up):
  python demo0-v2/explore_lite_py.py --map-topic /map
"""
import math, time, argparse, threading
import rclpy
from rclpy.executors import MultiThreadedExecutor
from geometry_msgs.msg import PoseStamped, Twist
from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult

from auto_explore import MapWatch, find_frontiers, yaw_to_quat   # reuse tested code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map-topic", default="/map")
    ap.add_argument("--costmap-topic", default="/global_costmap/costmap")
    ap.add_argument("--cost-threshold", type=int, default=50)
    ap.add_argument("--min-frontier", type=int, default=8, help="min frontier cluster size (cells); 8*0.05m ~= explore_lite min_frontier_size 0.4 m")
    ap.add_argument("--planner-frequency", type=float, default=0.5, help="Hz to re-evaluate frontiers (explore_lite default 0.5)")
    ap.add_argument("--potential-scale", type=float, default=1.0, help="distance weight in the cost (higher = prefer near)")
    ap.add_argument("--gain-scale", type=float, default=0.06, help="frontier-size weight (higher = prefer big openings)")
    ap.add_argument("--robot-radius", type=float, default=0.16)
    ap.add_argument("--noise-min", type=int, default=4)
    ap.add_argument("--blacklist-radius", type=float, default=0.35, help="a goal within this of a blacklisted point is skipped (m)")
    ap.add_argument("--goal-standoff", type=float, default=0.2, help="stop this far short of the frontier boundary (m)")
    ap.add_argument("--min-goal-dist", type=float, default=0.15)
    ap.add_argument("--retry-before-blacklist", type=int, default=3, help="fails to the SAME frontier before blacklisting (rides out transient transform aborts)")
    ap.add_argument("--goal-timeout", type=float, default=25.0, help="cancel a goal after this many s (never sit forever on one)")
    ap.add_argument("--no-progress-timeout", type=float, default=10.0, help="cancel if distance_remaining stops improving for this long (s)")
    ap.add_argument("--progress-eps", type=float, default=0.05, help="min distance improvement (m) to count as progress")
    ap.add_argument("--patience", type=int, default=6, help="consecutive empty re-evaluations before declaring done")
    ap.add_argument("--time-budget", type=float, default=1800.0)
    ap.add_argument("--no-return-home", action="store_true")
    ap.add_argument("--initial-spin", action="store_true",
                    help="rotate 360 in place at the start to map the surroundings -> generate frontiers "
                         "all around (helps RTAB, which tracks in-place rotation; skip for sparse 2D scan).")
    ap.add_argument("--spin-speed", type=float, default=0.3, help="initial-spin rotation speed (rad/s)")
    args = ap.parse_args()

    rclpy.init()
    watch = MapWatch(args.map_topic, args.costmap_topic)
    exe = MultiThreadedExecutor(); exe.add_node(watch)
    threading.Thread(target=exe.spin, daemon=True).start()
    nav = BasicNavigator()

    def gstamp():
        return rclpy.time.Time().to_msg()   # latest TF -> no future extrapolation

    def send(gx, gy, yaw):
        p = PoseStamped()
        p.header.frame_id = "map"; p.header.stamp = gstamp()
        p.pose.position.x = float(gx); p.pose.position.y = float(gy)
        _, _, qz, qw = yaw_to_quat(yaw)
        p.pose.orientation.z, p.pose.orientation.w = qz, qw
        nav.goToPose(p)

    def standoff(c, rx, ry):
        d = math.hypot(rx - c["x"], ry - c["y"])
        if d < 1e-3:
            return c["x"], c["y"]
        so = min(args.goal_standoff, max(0.0, d - 0.1))
        return c["x"] + (rx - c["x"]) / d * so, c["y"] + (ry - c["y"]) / d * so

    print(">>> waiting for /map and robot pose ...")
    while rclpy.ok():
        pose = watch.robot_pose()
        if watch.grid is not None and pose is not None:
            break
        time.sleep(0.5)
    print(f">>> mapping live. robot at ({pose[0]:+.2f}, {pose[1]:+.2f}). explore_lite-py running.")

    if args.initial_spin:
        print(f">>> initial 360 look-around ({args.spin_speed:.2f} rad/s) to seed frontiers...")
        tw = Twist(); tw.angular.z = args.spin_speed
        t0 = time.time(); dur = 2 * math.pi / args.spin_speed
        while time.time() - t0 < dur and rclpy.ok():
            watch.cmd_pub.publish(tw); time.sleep(0.05)
        watch.cmd_pub.publish(Twist()); time.sleep(0.5)
        print(">>> look-around done.")

    blacklist = []                 # [(x,y)] permanently-skipped spots
    fail_count = {}                # keyed by rounded (x,y) -> consecutive fails
    cur = None                     # (fx, fy) frontier we're currently driving to
    empty = 0
    period = 1.0 / max(0.05, args.planner_frequency)
    t_start = time.time()
    goals_sent = 0
    t_goal_sent = 0.0              # watchdog: when the current goal was sent
    best_d = None                 # smallest distance_remaining seen for the current goal
    t_prog = 0.0                  # last time the goal made real progress

    def key(x, y):
        return (round(x, 1), round(y, 1))

    try:
        while rclpy.ok():
            if time.time() - t_start > args.time_budget:
                print(">>> time budget reached; finishing."); break
            pose = watch.robot_pose()
            if pose is None or watch.grid is None:
                time.sleep(0.3); continue
            rx, ry, _ = pose

            # --- current goal: finished, or WATCHDOG (timeout/no-progress) so we never sit forever ---
            def record_fail(reason):
                k = key(*cur)
                fail_count[k] = fail_count.get(k, 0) + 1
                print(f"  [{reason}] ({cur[0]:+.2f},{cur[1]:+.2f}) attempt {fail_count[k]}/{args.retry_before_blacklist}")
                if fail_count[k] >= args.retry_before_blacklist:
                    blacklist.append(cur); print("    -> blacklisted (too many fails)")

            if cur is not None:
                if nav.isTaskComplete():
                    if nav.getResult() == TaskResult.SUCCEEDED:
                        fail_count[key(*cur)] = 0
                        blacklist.append(cur)      # reached -> don't loop back here
                        print(f"  [ok] reached frontier ({cur[0]:+.2f},{cur[1]:+.2f})")
                    else:
                        record_fail("fail")
                    cur = None
                else:
                    nowt = time.time()
                    fb = nav.getFeedback()
                    if fb is not None:
                        d = float(fb.distance_remaining)
                        if d > 0.0 and (best_d is None or d < best_d - args.progress_eps):
                            best_d = d; t_prog = nowt
                    stuck = best_d is not None and (nowt - t_prog) > args.no_progress_timeout
                    timed = (nowt - t_goal_sent) > args.goal_timeout
                    if stuck or timed:
                        nav.cancelTask(); time.sleep(0.3)
                        record_fail("stuck" if stuck else "timeout")
                        cur = None
                    else:
                        time.sleep(0.3); continue   # still driving + progressing -> let it, don't re-plan

            # --- (re)detect frontiers every tick (explore_lite style) ---
            res = watch.grid.info.resolution
            rr = max(1, int(round(args.robot_radius / res)))
            raw = find_frontiers(watch.grid, args.min_frontier, rr, args.noise_min)

            def ok_cost(c):
                cost = watch.cost_at(c["x"], c["y"])
                return cost is None or cost < 0 or cost < args.cost_threshold

            cands = [c for c in raw if ok_cost(c)
                     and all(math.hypot(c["x"] - bx, c["y"] - by) > args.blacklist_radius for bx, by in blacklist)
                     and math.hypot(c["x"] - rx, c["y"] - ry) >= args.min_goal_dist]

            if not cands:
                empty += 1
                print(f">>> no reachable frontier ({empty}/{args.patience}) — {len(raw)} raw")
                if empty >= args.patience:
                    print(">>> exploration complete."); break
                time.sleep(period); continue
            empty = 0

            # explore_lite cost: lower is better -> prefer NEAR and BIG
            def cost(c):
                d = math.hypot(c["x"] - rx, c["y"] - ry)
                return args.potential_scale * d - args.gain_scale * c["size"] * res
            best = min(cands, key=cost)
            tgt = (best["x"], best["y"])

            # only (re)send if we're not already driving to ~this frontier
            if cur is None or math.hypot(tgt[0] - cur[0], tgt[1] - cur[1]) > args.blacklist_radius:
                gx, gy = standoff(best, rx, ry)
                yaw = math.atan2(best["y"] - ry, best["x"] - rx)
                goals_sent += 1
                print(f">>> goal {goals_sent}: frontier ({tgt[0]:+.2f},{tgt[1]:+.2f}) via ({gx:+.2f},{gy:+.2f}) "
                      f"size={best['size']} [{len(cands)} cand]")
                send(gx, gy, yaw)
                cur = tgt
                t_goal_sent = time.time(); best_d = None; t_prog = time.time()
            time.sleep(period)

        if not args.no_return_home:
            print(">>> returning to origin (0,0)...")
            send(0.0, 0.0, 0.0)
            t0 = time.time()
            while not nav.isTaskComplete() and time.time() - t0 < 90:
                time.sleep(0.3)
            print(">>> back near start.")
    except KeyboardInterrupt:
        print(">>> interrupted."); nav.cancelTask()
    finally:
        try: watch.destroy_node()
        except Exception: pass
        rclpy.shutdown()
        print(">>> explore_lite-py done.")


if __name__ == "__main__":
    main()
