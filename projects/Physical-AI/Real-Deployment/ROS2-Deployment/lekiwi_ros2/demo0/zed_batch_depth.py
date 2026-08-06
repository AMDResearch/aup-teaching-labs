#!/usr/bin/env python3
"""Stage B: batch depth over a recorded session (offline, high quality).
Reads rec/<session>/frames/*.png (L|R side-by-side) + stamps.txt,
subsamples every --step, rectifies with factory calib, runs RAFT-Stereo,
and writes an RGB-D dataset for Stage C (RTAB-Map):
    <out>/rgb/%06d.png     (rectified left, bgr8)
    <out>/depth/%06d.png   (16UC1, depth in millimetres, 0=invalid)
    <out>/stamps.txt       (k  epoch_seconds)
    <out>/camera_info.txt  (fx fy cx cy width height)

Run (conda lerobot-new):
    PYTHONNOUSERSITE=1 python zed_batch_depth.py \
        --rec <repo>/lekiwi_ros2/demo0/rec/session20260703_164232 --step 3 --iters 32
"""
import os as _os  # repo-relative root (works from a git clone)
_ROS2 = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os
import sys, time, argparse, configparser, numpy as np, cv2, torch

REPO = os.path.join(_ROS2, "utils/RAFT-Stereo")
sys.path.append(REPO); sys.path.append(os.path.join(REPO, "core"))
from raft_stereo import RAFTStereo
from utils.utils import InputPadder

CALIB = os.path.join(_ROS2, "utils/calib.conf")
RES_WH = {"VGA": (672, 376), "HD": (1280, 720), "FHD": (1920, 1080), "2K": (2208, 1242)}

ap = argparse.ArgumentParser()
ap.add_argument("--rec", required=True)
ap.add_argument("--out", default=None)
ap.add_argument("--step", type=int, default=3)
ap.add_argument("--iters", type=int, default=32)
ap.add_argument("--res", default="VGA")
ap.add_argument("--ckpt", default=os.path.join(_ROS2, "utils/RAFT-Stereo/models/raftstereo-middlebury.pth"))
ap.add_argument("--max-depth", type=float, default=10.0)
args = ap.parse_args()
OUT = args.out or os.path.join(args.rec, "depth_out")
W, H = RES_WH[args.res]

# rectify
cp = configparser.ConfigParser(); cp.read(CALIB)
def K(sec):
    s = cp[sec]
    return (np.array([[float(s["fx"]),0,float(s["cx"])],[0,float(s["fy"]),float(s["cy"])],[0,0,1]]),
            np.array([float(s[k]) for k in ("k1","k2","p1","p2","k3")]))
Kl,Dl = K(f"LEFT_CAM_{args.res}"); Kr,Dr = K(f"RIGHT_CAM_{args.res}")
st = cp["STEREO"]; base = float(st["Baseline"])/1000.0
R,_ = cv2.Rodrigues(np.array([float(st[f"RX_{args.res}"]),float(st[f"CV_{args.res}"]),float(st[f"RZ_{args.res}"])]))
T = np.array([-float(st["Baseline"]),float(st["TY"]),float(st["TZ"])])/1000.0
R1,R2,P1,P2,Q,_,_ = cv2.stereoRectify(Kl,Dl,Kr,Dr,(W,H),R,T,flags=cv2.CALIB_ZERO_DISPARITY,alpha=0)
mlx,mly = cv2.initUndistortRectifyMap(Kl,Dl,R1,P1,(W,H),cv2.CV_32FC1)
mrx,mry = cv2.initUndistortRectifyMap(Kr,Dr,R2,P2,(W,H),cv2.CV_32FC1)
fx,fy,cx,cy = P1[0,0],P1[1,1],P1[0,2],P1[1,2]

# model
a = type("A",(),{})()
a.hidden_dims=[128,128,128]; a.corr_implementation="reg"; a.shared_backbone=False
a.corr_levels=4; a.corr_radius=4; a.n_downsample=2; a.context_norm="batch"
a.slow_fast_gru=False; a.n_gru_layers=3; a.mixed_precision=False
dev = "cuda" if torch.cuda.is_available() else "cpu"
model = RAFTStereo(a); sd = torch.load(args.ckpt, map_location="cpu")
model.load_state_dict({(k[7:] if k.startswith("module.") else k):v for k,v in sd.items()})
model = model.to(dev).eval()
print(f"device={dev} ckpt={os.path.basename(args.ckpt)} iters={args.iters} step={args.step}")

# read stamps
pairs = []
with open(os.path.join(args.rec, "stamps.txt")) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"): continue
        p = line.split(); pairs.append((int(p[0]), float(p[1])))
sel = pairs[::args.step]
print(f"total {len(pairs)} frames -> selected {len(sel)} (every {args.step})")

os.makedirs(os.path.join(OUT,"rgb"), exist_ok=True)
os.makedirs(os.path.join(OUT,"depth"), exist_ok=True)
with open(os.path.join(OUT,"camera_info.txt"),"w") as f:
    f.write(f"{fx} {fy} {cx} {cy} {W} {H}\n")
sf = open(os.path.join(OUT,"stamps.txt"),"w")

def infer(Lr,Rr):
    def tt(img): return torch.from_numpy(cv2.cvtColor(img,cv2.COLOR_BGR2RGB)).permute(2,0,1).float()[None].to(dev)
    with torch.no_grad():
        i1,i2 = tt(Lr),tt(Rr); pad=InputPadder(i1.shape,divis_by=32); i1,i2=pad.pad(i1,i2)
        _,fl = model(i1,i2,iters=args.iters,test_mode=True); fl=pad.unpad(fl).squeeze()
    disp = -fl.cpu().numpy()
    depth = np.zeros_like(disp, np.float32); m = disp>0.5
    depth[m] = fx*base/disp[m]
    depth[(depth<0.2)|(depth>args.max_depth)] = 0.0
    return depth

t0=time.time()
for k,(idx,stamp) in enumerate(sel):
    fp = os.path.join(args.rec,"frames",f"{idx:06d}.png")
    frame = cv2.imread(fp)
    if frame is None: print(f"skip missing {fp}"); continue
    Lr = cv2.remap(frame[:,:W], mlx,mly, cv2.INTER_LINEAR)
    Rr = cv2.remap(frame[:,W:], mrx,mry, cv2.INTER_LINEAR)
    depth = infer(Lr,Rr)
    depth_mm = np.clip(depth*1000.0, 0, 65535).astype(np.uint16)
    cv2.imwrite(os.path.join(OUT,"rgb",f"{k:06d}.png"), Lr)
    cv2.imwrite(os.path.join(OUT,"depth",f"{k:06d}.png"), depth_mm)
    sf.write(f"{k} {stamp:.6f}\n")
    if k % 20 == 0 or k == len(sel)-1:
        el=time.time()-t0; cov=100.0*(depth>0).mean()
        eta=(el/(k+1))*(len(sel)-k-1)
        print(f"  [{k+1}/{len(sel)}] cov {cov:.0f}%  {el:.0f}s elapsed  ETA {eta:.0f}s", flush=True)
sf.close()
print(f"DONE -> {OUT}  ({len(sel)} RGB-D frames)")
