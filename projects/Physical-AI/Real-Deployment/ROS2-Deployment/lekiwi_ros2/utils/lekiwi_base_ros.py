#!/usr/bin/env python3
"""Live LeKiwi base ROS2 driver for Nav2 (conda: rocm-free, needs feetech + rclpy).
Subscribes /cmd_vel (Twist: linear.x,y m/s ; angular.z rad/s, holonomic) -> wheels.
Reads Present_Velocity -> wheel odometry -> publishes /odom + TF odom->base_link.
Watchdog stops wheels if no /cmd_vel for --timeout s.

With --record <session_dir> it ALSO appends odom.txt in the exact format of
demo0/lekiwi_map_record.py, so an auto-explore run can be rebuilt offline by
demo0/build_map.sh (demo0-v2). Without it, behaviour is unchanged (demo1/2).

Run:
  source /opt/ros/jazzy/setup.bash; conda activate lerobot-new
  export ROS_DOMAIN_ID=0 PYTHONNOUSERSITE=1
  export PYTHONPATH=/opt/ros/jazzy/lib/python3.12/site-packages:$PYTHONPATH
  python lekiwi_base_ros.py
"""
import os, math, time, argparse
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist, TransformStamped
from nav_msgs.msg import Odometry
from tf2_ros import TransformBroadcaster
from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode
from lerobot.robots.lekiwi.lekiwi import LeKiwi

WHEELS = {"left_wheel": 7, "back_wheel": 8, "right_wheel": 9}


class Base(Node):
    def __init__(self, args):
        super().__init__("lekiwi_base")
        self.timeout = args.timeout
        self.bus = FeetechMotorsBus(port=args.port,
            motors={n: Motor(i, "sts3215", MotorNormMode.RANGE_M100_100) for n, i in WHEELS.items()})
        self.bus.connect(); self.bus.disable_torque(); self.bus.configure_motors()
        for n in WHEELS: self.bus.write("Operating_Mode", n, OperatingMode.VELOCITY.value)
        self.bus.enable_torque()
        self.cmd = (0.0, 0.0, 0.0); self.last_cmd = 0.0
        self.x = self.y = self.th = 0.0; self.t_prev = time.time()
        # optional recording of odom.txt (demo0-v2 auto-explore -> build_map.sh)
        self.odom_f = None
        if args.record:
            os.makedirs(args.record, exist_ok=True)
            self.odom_f = open(os.path.join(args.record, "odom.txt"), "w")
            self.odom_f.write("# t x_m y_m theta_rad vx_mps vy_mps vtheta_radps  (wheel odometry, velocity-integrated)\n")
            self.get_logger().info(f"recording odom -> {os.path.join(args.record, 'odom.txt')}")
        self.create_subscription(Twist, "/cmd_vel", self.on_cmd, 10)
        self.pub_odom = self.create_publisher(Odometry, "/odom", 20)
        self.tfb = TransformBroadcaster(self)
        self.create_timer(0.033, self.loop)   # ~30 Hz
        self.get_logger().info("lekiwi_base ready: /cmd_vel -> wheels, publishing /odom + TF")

    def on_cmd(self, m):
        self.cmd = (m.linear.x, m.linear.y, math.degrees(m.angular.z))  # deg/s for _body_to_wheel_raw
        self.last_cmd = time.time()

    def loop(self):
        vx_c, vy_c, wz_c = self.cmd
        if (time.time() - self.last_cmd) > self.timeout: vx_c = vy_c = wz_c = 0.0
        try:
            if (vx_c, vy_c, wz_c) == (0.0, 0.0, 0.0):
                self.bus.sync_write("Goal_Velocity", dict.fromkeys(WHEELS, 0))
            else:
                w = LeKiwi._body_to_wheel_raw(LeKiwi, vx_c, vy_c, wz_c)
                self.bus.sync_write("Goal_Velocity", {"left_wheel": w["base_left_wheel"],
                    "back_wheel": w["base_back_wheel"], "right_wheel": w["base_right_wheel"]})
            pv = self.bus.sync_read("Present_Velocity", list(WHEELS), normalize=False)
            db = 22  # deadband: ignore idle encoder-velocity noise to reduce odom drift
            lw = 0 if abs(pv["left_wheel"]) < db else pv["left_wheel"]
            bw = 0 if abs(pv["back_wheel"]) < db else pv["back_wheel"]
            rw = 0 if abs(pv["right_wheel"]) < db else pv["right_wheel"]
            bv = LeKiwi._wheel_raw_to_body(LeKiwi, lw, bw, rw)
            vx, vy, vth = bv["x.vel"], bv["y.vel"], math.radians(bv["theta.vel"])
        except Exception as e:
            self.get_logger().warn(f"bus: {e}"); return
        now = time.time(); dt = now - self.t_prev; self.t_prev = now
        self.x += (vx*math.cos(self.th) - vy*math.sin(self.th))*dt
        self.y += (vx*math.sin(self.th) + vy*math.cos(self.th))*dt
        self.th += vth*dt
        stamp = self.get_clock().now().to_msg()
        qz = math.sin(self.th/2); qw = math.cos(self.th/2)
        tf = TransformStamped(); tf.header.stamp = stamp; tf.header.frame_id = "odom"; tf.child_frame_id = "base_link"
        tf.transform.translation.x = self.x; tf.transform.translation.y = self.y
        tf.transform.rotation.z = qz; tf.transform.rotation.w = qw
        self.tfb.sendTransform(tf)
        od = Odometry(); od.header.stamp = stamp; od.header.frame_id = "odom"; od.child_frame_id = "base_link"
        od.pose.pose.position.x = self.x; od.pose.pose.position.y = self.y
        od.pose.pose.orientation.z = qz; od.pose.pose.orientation.w = qw
        od.twist.twist.linear.x = vx; od.twist.twist.linear.y = vy; od.twist.twist.angular.z = vth
        self.pub_odom.publish(od)
        if self.odom_f is not None:
            self.odom_f.write(f"{now:.6f} {self.x:.4f} {self.y:.4f} {self.th:.5f} {vx:.4f} {vy:.4f} {vth:.5f}\n")

    def shutdown(self):
        try: self.bus.sync_write("Goal_Velocity", dict.fromkeys(WHEELS, 0)); self.bus.disable_torque(); self.bus.disconnect()
        except Exception: pass
        if self.odom_f is not None:
            try: self.odom_f.flush(); self.odom_f.close()
            except Exception: pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--timeout", type=float, default=0.5)
    ap.add_argument("--record", default=None, help="session dir to append odom.txt (demo0-v2 auto-explore)")
    args = ap.parse_args()
    rclpy.init(); n = Base(args)
    try: rclpy.spin(n)
    except KeyboardInterrupt: pass
    finally: n.shutdown(); n.destroy_node(); rclpy.shutdown()

if __name__ == "__main__": main()
