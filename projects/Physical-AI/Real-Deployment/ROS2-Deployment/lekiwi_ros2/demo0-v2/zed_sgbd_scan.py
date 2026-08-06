#!/usr/bin/env python3
"""demo0-v2 REAL-TIME depth for the 2D-scan pipeline — ZED stereo -> OpenCV StereoSGBM.

A drop-in replacement for utils/zed_rgbd_ros2.py that publishes the SAME topics
  <ns>/rgb/image  <ns>/rgb/camera_info  <ns>/depth/image (32FC1 metres, 0=invalid)
but computes depth with cv2.StereoSGBM instead of RAFT-Stereo. SGBM is far noisier than
RAFT but runs at 20-30+ FPS on VGA (no GPU, no ~80 s warmup), which is all a 2D occupancy
LaserScan needs (min-distance-per-column). This makes depth->laserscan->slam_toolbox truly
real-time. SGBM's uniquenessRatio + speckle filter already void low-texture / uncertain
pixels, so textureless walls become holes (like our RAFT texture filter) — no phantom floor.

Run (conda lerobot-new — cv2 + numpy + rclpy, NO torch):
  source /opt/ros/jazzy/setup.bash; conda activate lerobot-new
  export PYTHONPATH=/opt/ros/jazzy/lib/python3.12/site-packages:$PYTHONPATH
  python demo0-v2/zed_sgbd_scan.py --dev 4
"""
import os as _os  # repo-relative root (works from a git clone)
_ROS2 = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os, sys, time, argparse, configparser, numpy as np, cv2
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CameraInfo
from std_msgs.msg import Header

CALIB = os.path.join(_ROS2, "utils/calib.conf")
RES_WH = {"VGA": (672, 376), "HD": (1280, 720), "FHD": (1920, 1080), "2K": (2208, 1242)}


def build_rectify(res):
    cp = configparser.ConfigParser(); cp.read(CALIB)
    def K(sec):
        s = cp[sec]
        return (np.array([[float(s["fx"]), 0, float(s["cx"])], [0, float(s["fy"]), float(s["cy"])], [0, 0, 1]]),
                np.array([float(s[k]) for k in ("k1", "k2", "p1", "p2", "k3")]))
    W, H = RES_WH[res]
    Kl, Dl = K(f"LEFT_CAM_{res}"); Kr, Dr = K(f"RIGHT_CAM_{res}")
    st = cp["STEREO"]; base = float(st["Baseline"]) / 1000.0
    R, _ = cv2.Rodrigues(np.array([float(st[f"RX_{res}"]), float(st[f"CV_{res}"]), float(st[f"RZ_{res}"])]))
    T = np.array([-float(st["Baseline"]), float(st["TY"]), float(st["TZ"])]) / 1000.0
    R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(Kl, Dl, Kr, Dr, (W, H), R, T, flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
    mlx, mly = cv2.initUndistortRectifyMap(Kl, Dl, R1, P1, (W, H), cv2.CV_32FC1)
    mrx, mry = cv2.initUndistortRectifyMap(Kr, Dr, R2, P2, (W, H), cv2.CV_32FC1)
    return (W, H, mlx, mly, mrx, mry, P1, base)


class ZedSGBM(Node):
    def __init__(self, args):
        super().__init__("zed_sgbd_scan")
        self.args = args
        self.W, self.H, self.mlx, self.mly, self.mrx, self.mry, self.P1, self.base = build_rectify(args.res)
        # SGBM: numDisparities must be divisible by 16. 144 covers ~0.3-3 m at VGA (fx 329, base 0.12).
        bs = args.block
        self.sgbm = cv2.StereoSGBM_create(
            minDisparity=0, numDisparities=args.num_disp, blockSize=bs,
            P1=8 * bs * bs, P2=32 * bs * bs, disp12MaxDiff=1,
            uniquenessRatio=args.uniqueness, speckleWindowSize=args.speckle_win, speckleRange=2,
            preFilterCap=63, mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)
        self.cap = cv2.VideoCapture(args.dev, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.W * 2)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.H)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # only keep the newest frame -> low latency
        #   (a lagging scan makes slam's map->odom lag -> Nav2 "extrapolation into future" aborts)
        self.frame_id = args.frame_id
        ns = args.ns.rstrip("/")
        self.pub_rgb = self.create_publisher(Image, f"{ns}/rgb/image", 2)
        self.pub_ci = self.create_publisher(CameraInfo, f"{ns}/rgb/camera_info", 2)
        self.pub_dep = self.create_publisher(Image, f"{ns}/depth/image", 2)
        self.ci = self._camera_info()
        self.n = 0
        self.get_logger().info(f"SGBM depth streaming ({self.W}x{self.H}, numDisp {args.num_disp}).")
        self.create_timer(0.005, self.tick)   # run flat-out; SGBM is the (small) bottleneck

    def _camera_info(self):
        ci = CameraInfo(); ci.width = self.W; ci.height = self.H
        ci.distortion_model = "plumb_bob"; ci.d = [0.0] * 5
        fx, fy, cx, cy = self.P1[0, 0], self.P1[1, 1], self.P1[0, 2], self.P1[1, 2]
        ci.k = [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]
        ci.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        ci.p = [float(x) for x in self.P1.reshape(-1)]
        return ci

    def _img_msg(self, stamp, arr, encoding, step):
        m = Image(); m.header = Header(); m.header.stamp = stamp; m.header.frame_id = self.frame_id
        m.height, m.width = arr.shape[0], arr.shape[1]
        m.encoding = encoding; m.is_bigendian = 0; m.step = step; m.data = arr.tobytes()
        return m

    def tick(self):
        ok = frame = None
        for _ in range(2):
            ok, frame = self.cap.read()
        if not ok or frame is None:
            return
        left, right = frame[:, :self.W], frame[:, self.W:]
        Lr = cv2.remap(left, self.mlx, self.mly, cv2.INTER_LINEAR)
        Rr = cv2.remap(right, self.mrx, self.mry, cv2.INTER_LINEAR)
        gl = cv2.cvtColor(Lr, cv2.COLOR_BGR2GRAY); gr = cv2.cvtColor(Rr, cv2.COLOR_BGR2GRAY)
        disp = self.sgbm.compute(gl, gr).astype(np.float32) / 16.0   # SGBM returns fixed-point x16
        depth = np.zeros_like(disp, np.float32)
        m = disp > 1.0
        depth[m] = self.P1[0, 0] * self.base / disp[m]
        depth[(depth < 0.2) | (depth > self.args.max_depth)] = 0.0
        stamp = self.get_clock().now().to_msg()
        rgb = cv2.cvtColor(Lr, cv2.COLOR_BGR2RGB)
        self.ci.header.stamp = stamp; self.ci.header.frame_id = self.frame_id
        self.pub_rgb.publish(self._img_msg(stamp, rgb, "rgb8", self.W * 3))
        self.pub_dep.publish(self._img_msg(stamp, depth.astype(np.float32), "32FC1", self.W * 4))
        self.pub_ci.publish(self.ci)
        self.n += 1
        if self.n % 30 == 0:
            cov = 100.0 * (depth > 0).mean()
            md = np.median(depth[depth > 0]) if (depth > 0).any() else 0.0
            self.get_logger().info(f"frame {self.n}  depth coverage {cov:.0f}%  median {md:.2f}m")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", type=int, default=0)
    ap.add_argument("--res", default="VGA")
    ap.add_argument("--ns", default="/zed")
    ap.add_argument("--frame-id", default="camera_optical_frame")
    ap.add_argument("--max-depth", type=float, default=3.0)
    ap.add_argument("--num-disp", type=int, default=144, help="disparity range (÷16); 144 ~ 0.3-3 m at VGA")
    ap.add_argument("--block", type=int, default=7, help="SGBM block size (odd)")
    ap.add_argument("--uniqueness", type=int, default=12, help="higher = reject more uncertain matches")
    ap.add_argument("--speckle-win", type=int, default=120, help="remove blobs smaller than this")
    args = ap.parse_args()
    rclpy.init()
    n = ZedSGBM(args)
    try:
        rclpy.spin(n)
    except KeyboardInterrupt:
        pass
    finally:
        n.cap.release(); n.destroy_node(); rclpy.shutdown()


if __name__ == "__main__":
    main()
