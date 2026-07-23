#!/usr/bin/env python3
"""ZED 2i RGB-D publisher for ROS2 — runs in conda (rocm torch + rclpy).
capture UVC stereo -> rectify (factory calib) -> RAFT-Stereo depth (gfx1152) -> publish:
  <ns>/rgb/image        sensor_msgs/Image  rgb8   (rectified left)
  <ns>/rgb/camera_info  sensor_msgs/CameraInfo
  <ns>/depth/image      sensor_msgs/Image  32FC1  (meters, aligned to rgb, 0=invalid)

Run:
  source /opt/ros/jazzy/setup.bash
  conda activate lerobot-new
  export ROS_DOMAIN_ID=0 PYTHONNOUSERSITE=1
  export PYTHONPATH=/opt/ros/jazzy/lib/python3.12/site-packages:$PYTHONPATH
  python zed_rgbd_ros2.py --iters 16
"""
import os as _os  # repo-relative root (works from a git clone)
_ROS2 = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os
import sys, time, argparse, configparser, threading, queue, numpy as np, cv2

REPO = os.path.join(_ROS2, "utils/RAFT-Stereo")

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CameraInfo
from std_msgs.msg import Header

# IMPORTANT (ROCm 7.13): torch + RAFT-Stereo are imported LAZILY, inside main() AFTER
# rclpy.init(). Importing torch BEFORE rclpy.init() corrupts the heap ("free(): invalid
# size") and aborts the process. Initialising rclpy first, then torch, avoids the clash.
torch = RAFTStereo = InputPadder = None


def _load_torch_and_raft():
    global torch, RAFTStereo, InputPadder
    import torch as _torch
    sys.path.append(REPO); sys.path.append(os.path.join(REPO, "core"))
    from raft_stereo import RAFTStereo as _RAFTStereo
    from utils.utils import InputPadder as _InputPadder
    torch, RAFTStereo, InputPadder = _torch, _RAFTStereo, _InputPadder

CALIB = os.path.join(_ROS2, "utils/calib.conf")
RES_WH = {"VGA": (672, 376), "HD": (1280, 720), "FHD": (1920, 1080), "2K": (2208, 1242)}


def build_rectify(res):
    cp = configparser.ConfigParser(); cp.read(CALIB)
    def K(sec):
        s = cp[sec]
        return (np.array([[float(s["fx"]),0,float(s["cx"])],[0,float(s["fy"]),float(s["cy"])],[0,0,1]]),
                np.array([float(s[k]) for k in ("k1","k2","p1","p2","k3")]))
    W, H = RES_WH[res]
    Kl,Dl = K(f"LEFT_CAM_{res}"); Kr,Dr = K(f"RIGHT_CAM_{res}")
    st = cp["STEREO"]; base = float(st["Baseline"])/1000.0
    R,_ = cv2.Rodrigues(np.array([float(st[f"RX_{res}"]),float(st[f"CV_{res}"]),float(st[f"RZ_{res}"])]))
    T = np.array([-float(st["Baseline"]),float(st["TY"]),float(st["TZ"])])/1000.0
    R1,R2,P1,P2,Q,_,_ = cv2.stereoRectify(Kl,Dl,Kr,Dr,(W,H),R,T,flags=cv2.CALIB_ZERO_DISPARITY,alpha=0)
    mlx,mly = cv2.initUndistortRectifyMap(Kl,Dl,R1,P1,(W,H),cv2.CV_32FC1)
    mrx,mry = cv2.initUndistortRectifyMap(Kr,Dr,R2,P2,(W,H),cv2.CV_32FC1)
    return (W,H,mlx,mly,mrx,mry,P1,base)


def load_model(ckpt, dev):
    a = type("A",(),{})()
    a.hidden_dims=[128,128,128]; a.corr_implementation="reg"; a.shared_backbone=False
    a.corr_levels=4; a.corr_radius=4; a.n_downsample=2; a.context_norm="batch"
    a.slow_fast_gru=False; a.n_gru_layers=3; a.mixed_precision=False
    m = RAFTStereo(a); sd = torch.load(ckpt, map_location="cpu")
    m.load_state_dict({(k[7:] if k.startswith("module.") else k):v for k,v in sd.items()})
    return m.to(dev).eval()


class ZedRGBD(Node):
    def __init__(self, args):
        super().__init__("zed_rgbd")
        self.args = args
        self.W,self.H,self.mlx,self.mly,self.mrx,self.mry,self.P1,self.base = build_rectify(args.res)
        self.dev = "cuda" if torch.cuda.is_available() else "cpu"
        self.get_logger().info(f"device={self.dev} loading {os.path.basename(args.ckpt)} ...")
        self.model = load_model(args.ckpt, self.dev)
        self.cap = cv2.VideoCapture(args.dev, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.W*2)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.H)
        self.frame_id = args.frame_id
        ns = args.ns.rstrip("/")
        self.pub_rgb  = self.create_publisher(Image, f"{ns}/rgb/image", 2)
        self.pub_ci   = self.create_publisher(CameraInfo, f"{ns}/rgb/camera_info", 2)
        self.pub_dep  = self.create_publisher(Image, f"{ns}/depth/image", 2)
        self.ci = self._camera_info()
        # warm up model (first call triggers MIOpen compile ~80s)
        self.get_logger().info("warming up RAFT (first run compiles MIOpen kernels, ~80s)...")
        self._infer(np.zeros((self.H,self.W,3),np.uint8), np.zeros((self.H,self.W,3),np.uint8))
        self.get_logger().info("warmup done, streaming.")
        self.n = 0
        # optional raw-frame recording (demo0-v2 auto-explore -> build_map.sh).
        # The camera can only be opened once, so recording piggybacks on this node.
        self.rec_idx = 0; self.rec_q = None; self.rec_stamp_f = None
        if args.record:
            recdir = args.record; frdir = os.path.join(recdir, "frames")
            os.makedirs(frdir, exist_ok=True)
            self.rec_stamp_f = open(os.path.join(recdir, "camera_stamps.txt"), "w")
            self.rec_stamp_f.write(f"# W={self.W} H={self.H} side-by-side width {2*self.W}\n")
            self.rec_q = queue.Queue(maxsize=512); self.rec_dir = frdir
            threading.Thread(target=self._rec_writer, daemon=True).start()
            self.get_logger().info(f"recording raw frames -> {frdir}")
        self.create_timer(0.01, self.tick)   # run back-to-back; depth is the bottleneck

    def _rec_writer(self):
        while True:
            try: i, fr = self.rec_q.get(timeout=0.5)
            except queue.Empty: continue
            if i is None: break
            cv2.imwrite(os.path.join(self.rec_dir, f"{i:06d}.png"), fr)

    def _camera_info(self):
        ci = CameraInfo(); ci.width=self.W; ci.height=self.H
        ci.distortion_model="plumb_bob"; ci.d=[0.0]*5
        fx,fy,cx,cy = self.P1[0,0],self.P1[1,1],self.P1[0,2],self.P1[1,2]
        ci.k=[fx,0.0,cx, 0.0,fy,cy, 0.0,0.0,1.0]
        ci.r=[1.0,0.0,0.0, 0.0,1.0,0.0, 0.0,0.0,1.0]
        ci.p=[float(x) for x in self.P1.reshape(-1)]
        return ci

    def _infer(self, Lr, Rr):
        def tt(img): return torch.from_numpy(cv2.cvtColor(img,cv2.COLOR_BGR2RGB)).permute(2,0,1).float()[None].to(self.dev)
        with torch.no_grad():
            i1,i2 = tt(Lr),tt(Rr); pad=InputPadder(i1.shape,divis_by=32); i1,i2=pad.pad(i1,i2)
            _,fl = self.model(i1,i2,iters=self.args.iters,test_mode=True); fl=pad.unpad(fl).squeeze()
        disp = -fl.cpu().numpy()
        depth = np.zeros_like(disp, np.float32); m = disp>0.5
        depth[m] = self.P1[0,0]*self.base/disp[m]
        depth[(depth<0.2)|(depth>self.args.max_depth)] = 0.0
        if self.args.depth_filter:
            depth = self._filter_depth(depth, disp, Lr, Rr)
        return depth

    def _filter_depth(self, depth, disp, Lr, Rr):
        """Drop LOW-CONFIDENCE RAFT depth so it can't become a phantom obstacle in the live
        costmap (demo0-v2 only; off by default so demo1/2 are untouched). RAFT emits a dense
        disparity for EVERY pixel — including textureless floor/blank walls where it hallucinates
        a smooth-but-wrong depth that projects into the 0.05-0.6 m obstacle band right in front of
        the robot and jams the MPPI controller. We prefer HOLES (unknown, which Nav2 can plan
        through) over phantom obstacles, so we zero depth where the match is untrustworthy.

        1) TEXTURE mask  — the main win: stereo is unreliable where the left image has no texture,
           which is exactly where the hallucinated floor/wall phantoms come from. Low local gradient
           -> drop. (Textureless holes become unknown; the wall's textured EDGES still anchor it.)
        2) PHOTOMETRIC L-R consistency — warp right->left by disp; large colour error = occlusion or
           a bad match at a depth discontinuity -> drop.
        3) SPECKLE removal — drop tiny isolated valid blobs (leftover depth noise).
        The final high-quality map is rebuilt OFFLINE (demo0/zed_batch_depth.py, unfiltered), so
        holes here cost nothing there — this only cleans the throwaway navigation costmap.
        """
        H, W = disp.shape
        # (1) texture mask: mean Sobel-gradient magnitude in a small window; drop low-texture pixels
        gray = cv2.cvtColor(Lr, cv2.COLOR_BGR2GRAY)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        texture = cv2.boxFilter(cv2.magnitude(gx, gy), -1, (7, 7))
        depth[texture < self.args.tex_thresh] = 0.0
        # (2) photometric left-right consistency: sample right image at (x - disp), compare to left
        xs = np.tile(np.arange(W, dtype=np.float32), (H, 1))
        ys = np.tile(np.arange(H, dtype=np.float32)[:, None], (1, W))
        warpR = cv2.remap(Rr, xs - disp, ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        err = np.abs(Lr.astype(np.int16) - warpR.astype(np.int16)).mean(2)
        depth[err > self.args.photo_thresh] = 0.0
        # (3) speckle removal: drop connected valid blobs smaller than speckle_min cells
        valid = (depth > 0).astype(np.uint8)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(valid, 8)
        if n > 1:
            small = np.array([False] + [stats[i, cv2.CC_STAT_AREA] < 50 for i in range(1, n)])
            depth[small[lab]] = 0.0
        return depth

    def _img_msg(self, stamp, arr, encoding, step):
        msg = Image(); msg.header = Header(); msg.header.stamp = stamp; msg.header.frame_id = self.frame_id
        msg.height, msg.width = arr.shape[0], arr.shape[1]
        msg.encoding = encoding; msg.is_bigendian = 0; msg.step = step
        msg.data = arr.tobytes()
        return msg

    def tick(self):
        ok = False; frame=None
        for _ in range(2): ok,frame = self.cap.read()
        if not ok or frame is None: return
        if self.rec_q is not None:   # stash raw side-by-side frame (pre-rectify) for offline rebuild
            try:
                self.rec_q.put_nowait((self.rec_idx, frame.copy()))
                self.rec_stamp_f.write(f"{self.rec_idx} {time.time():.6f}\n"); self.rec_idx += 1
            except queue.Full:
                pass
        left, right = frame[:, :self.W], frame[:, self.W:]
        Lr = cv2.remap(left, self.mlx,self.mly, cv2.INTER_LINEAR)
        Rr = cv2.remap(right, self.mrx,self.mry, cv2.INTER_LINEAR)
        depth = self._infer(Lr, Rr)
        stamp = self.get_clock().now().to_msg()
        rgb = cv2.cvtColor(Lr, cv2.COLOR_BGR2RGB)
        self.ci.header.stamp = stamp; self.ci.header.frame_id = self.frame_id
        self.pub_rgb.publish(self._img_msg(stamp, rgb, "rgb8", self.W*3))
        self.pub_dep.publish(self._img_msg(stamp, depth.astype(np.float32), "32FC1", self.W*4))
        self.pub_ci.publish(self.ci)
        self.n += 1
        if self.n % 10 == 0:
            cov = 100.0*(depth>0).mean()
            self.get_logger().info(f"frame {self.n}  depth coverage {cov:.0f}%  median {np.median(depth[depth>0]):.2f}m")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", type=int, default=0)
    ap.add_argument("--res", default="VGA")
    ap.add_argument("--ns", default="/zed")
    ap.add_argument("--frame-id", default="camera_optical_frame")
    ap.add_argument("--ckpt", default=os.path.join(_ROS2, "utils/RAFT-Stereo/models/raftstereo-middlebury.pth"))
    ap.add_argument("--iters", type=int, default=16)
    ap.add_argument("--max-depth", type=float, default=10.0)
    # demo0-v2 live-costmap depth cleaning (OFF by default -> demo1/2 unchanged). See _filter_depth.
    ap.add_argument("--depth-filter", action="store_true",
                    help="drop low-confidence RAFT depth (texture + L-R + speckle) so textureless "
                         "hallucination can't box the robot in during autonomous exploration")
    ap.add_argument("--tex-thresh", type=float, default=8.0,
                    help="min local gradient to trust a pixel's depth (lower=keep more, risk phantoms)")
    ap.add_argument("--photo-thresh", type=float, default=18.0,
                    help="max left-right colour error (0-255) before a pixel's depth is dropped")
    ap.add_argument("--record", default=None, help="session dir to dump raw frames + camera_stamps.txt (demo0-v2)")
    args = ap.parse_args()
    rclpy.init()                 # MUST come before importing torch (ROCm 7.13 heap clash)
    _load_torch_and_raft()
    n = ZedRGBD(args)
    try: rclpy.spin(n)
    except KeyboardInterrupt: pass
    finally:
        if n.rec_q is not None:
            try:
                n.rec_q.put_nowait((None, None))
            except queue.Full:
                pass
            n.rec_stamp_f.flush(); n.rec_stamp_f.close()
        n.cap.release(); n.destroy_node(); rclpy.shutdown()

if __name__ == "__main__":
    main()
