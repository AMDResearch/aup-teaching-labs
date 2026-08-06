#!/usr/bin/env python3
"""Build a Nav2 occupancy grid (pgm+yaml) from an RTAB-Map point cloud + robot odom path.
- Free: cells the robot actually drove through (odom path, dilated by robot radius) + floor points.
- Occupied: cells with DENSE obstacle points in the robot-height band (count threshold; removes smear),
            but NOT on the driven corridor (robot proved it traversable).
- Unknown: everything else.
"""
import os as _os  # repo-relative root (works from a git clone)
_ROS2 = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import sys, os, numpy as np, cv2, yaml

PLY  = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/.ros/map2d_cloud.ply")
ODOM = os.path.expanduser(sys.argv[2] if len(sys.argv) > 2 else "<repo>/lekiwi_ros2/demo0/rec/map20260704_114742/odom.txt")
OUT  = os.path.expanduser(sys.argv[3] if len(sys.argv) > 3 else "<repo>/lekiwi_ros2/utils/scene_map")
RES = 0.05
OBS_LO, OBS_HI = 0.13, 0.6
FLOOR_HI = 0.10
PAD = 0.6
OBS_MIN_COUNT = 110     # stricter: only dense real obstacles
ROBOT_R_CELLS = 6       # ~0.30 m free corridor half-width

with open(PLY, "rb") as f:
    assert f.readline().strip() == b"ply"
    fmt = f.readline().strip().decode(); props = []; n = 0; cur = None
    tmap = {"float":"f4","float32":"f4","double":"f8","uchar":"u1","uint8":"u1","int":"i4"}
    while True:
        ln = f.readline().strip().decode()
        if ln.startswith("element"):
            p = ln.split(); cur = p[1]
            if cur == "vertex": n = int(p[-1])
        elif ln.startswith("property") and cur == "vertex":
            _, t, nm = ln.split(); props.append((nm, ("<" if "little" in fmt else ">") + tmap[t]))
        elif ln == "end_header": break
    data = np.frombuffer(f.read(n * np.dtype(props).itemsize), dtype=np.dtype(props), count=n)
x, y, z = data["x"].astype(float), data["y"].astype(float), data["z"].astype(float)

px = []; py = []
for line in open(ODOM):
    line = line.strip()
    if not line or line.startswith("#"): continue
    p = line.split(); px.append(float(p[1])); py.append(float(p[2]))
px = np.array(px); py = np.array(py)

allx = np.concatenate([x, px]); ally = np.concatenate([y, py])
xmin, xmax = allx.min()-PAD, allx.max()+PAD
ymin, ymax = ally.min()-PAD, ally.max()+PAD
W = int(np.ceil((xmax-xmin)/RES)); H = int(np.ceil((ymax-ymin)/RES))

def cell(ax, ay):
    return (np.clip(((ax-xmin)/RES).astype(int),0,W-1), np.clip(((ay-ymin)/RES).astype(int),0,H-1))

obs = (z>=OBS_LO)&(z<=OBS_HI); flr = (z<FLOOR_HI)
oc = np.zeros((H,W),np.int32); fc = np.zeros((H,W),np.int32)
ox,oy = cell(x[obs],y[obs]); np.add.at(oc,(oy,ox),1)
fx,fy = cell(x[flr],y[flr]); np.add.at(fc,(fy,fx),1)

grid = np.full((H,W),-1,np.int8)
grid[fc>0] = 0                                   # floor -> free
grid[(oc>=OBS_MIN_COUNT)&(oc>fc)] = 100          # dense obstacle -> occupied

# driven corridor -> guaranteed free (overrides obstacles: robot proved traversable)
traj = np.zeros((H,W),np.uint8); tx,ty = cell(px,py)
for a,b in zip(tx,ty): traj[b,a] = 1
traj = cv2.dilate(traj, cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*ROBOT_R_CELLS+1,)*2))
grid[traj>0] = 0

# thicken remaining obstacles a touch
occ = cv2.dilate((grid==100).astype(np.uint8), np.ones((3,3),np.uint8))
grid[(occ>0)&(traj==0)] = 100

print(f"grid {W}x{H} x[{xmin:.2f},{xmax:.2f}] y[{ymin:.2f},{ymax:.2f}]  free={ (grid==0).sum() } occ={ (grid==100).sum() } unk={ (grid==-1).sum() }")

img = np.full((H,W),205,np.uint8); img[grid==0]=254; img[grid==100]=0; img=np.flipud(img)
cv2.imwrite(OUT+".pgm", img)
yaml.safe_dump({"image":os.path.basename(OUT)+".pgm","resolution":RES,
    "origin":[float(xmin),float(ymin),0.0],"negate":0,
    "occupied_thresh":0.65,"free_thresh":0.25}, open(OUT+".yaml","w"), sort_keys=False)
cv2.imwrite(OUT+"_preview.png", cv2.resize(cv2.cvtColor(img,cv2.COLOR_GRAY2BGR),(W*3,H*3),interpolation=cv2.INTER_NEAREST))
print(f"saved {OUT}.pgm/.yaml  origin=({xmin:.2f},{ymin:.2f})")
