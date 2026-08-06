#!/usr/bin/env python3
"""Minimal WASD holonomic teleop -> /cmd_vel (geometry_msgs/Twist).

For the demo0-v2 2D-scan manual driving. Pure rclpy — no camera, no conda needed.
LeKiwi is omni-directional, so A/D strafe sideways (not turn).

  w / s : forward / back            a / d : strafe left / right
  q / e : rotate left / right       space or k : stop
  z / x : slower / faster           Ctrl-C : quit
Hold a key to move; release (~0.4 s no key) auto-stops (keeps the base watchdog happy).
"""
import sys, termios, tty, select, time, argparse
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist

MOVE = {'w': (1, 0, 0), 's': (-1, 0, 0), 'a': (0, 1, 0), 'd': (0, -1, 0),
        'q': (0, 0, 1), 'e': (0, 0, -1)}


def get_key(timeout):
    r, _, _ = select.select([sys.stdin], [], [], timeout)
    return sys.stdin.read(1) if r else ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--speed', type=float, default=0.12, help='linear m/s')
    ap.add_argument('--turn', type=float, default=0.5, help='angular rad/s')
    args = ap.parse_args()
    rclpy.init()
    node = Node('wasd_teleop')
    pub = node.create_publisher(Twist, '/cmd_vel', 10)
    speed, turn = args.speed, args.turn

    print(__doc__)
    print(f"speed={speed:.2f} m/s  turn={turn:.2f} rad/s\n")
    settings = termios.tcgetattr(sys.stdin)
    cur = (0, 0, 0); last_move = 0.0
    try:
        tty.setraw(sys.stdin.fileno())
        while rclpy.ok():
            k = get_key(0.1)
            now = time.monotonic()
            if k == '\x03':                       # Ctrl-C
                break
            elif k in MOVE:
                cur = MOVE[k]; last_move = now
            elif k in (' ', 'k'):
                cur = (0, 0, 0)
            elif k == 'z':
                speed *= 0.9; turn *= 0.9
                sys.stdout.write(f"\r speed={speed:.2f} turn={turn:.2f}      \r\n")
            elif k == 'x':
                speed *= 1.1; turn *= 1.1
                sys.stdout.write(f"\r speed={speed:.2f} turn={turn:.2f}      \r\n")
            if now - last_move > 0.4:             # release-to-stop
                cur = (0, 0, 0)
            t = Twist()
            t.linear.x = float(cur[0] * speed)
            t.linear.y = float(cur[1] * speed)
            t.angular.z = float(cur[2] * turn)
            pub.publish(t)
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
        pub.publish(Twist())                      # stop
        node.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    main()
