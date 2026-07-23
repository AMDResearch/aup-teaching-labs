#!/usr/bin/env python3
"""Localize colored blocks in the map frame from an offline RGB-D session + wheel odometry.
For each depth_out frame: interpolate odom -> camera pose (base_link + static mount),
HSV-detect each block color, back-project centroid depth -> 3D, transform to map frame,
keep near-floor detections, cluster per color -> block (x,y).
"""
import os as _os  # repo-relative root (works from a git clone)
_ROS2 = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os, math, argparse, numpy as np, cv2

ap = argparse.ArgumentParser()
ap.add_argument("--session", default=os.path.join(_ROS2, "demo0/rec/map20260703_213624"))
ap.add_argument("--cam-fwd", type=float, default=0.14)   # base_link -> camera, forward (m); plate edge 0.125 + ZED body
ap.add_argument("--cam-up", type=float, default=0.11)    # up (m); measured lens height ~11cm
ap.add_argument("--floor-min", type=float, default=-0.10)
ap.add_argument("--floor-max", type=float, default=0.30) # block tops within this z of ground
args = ap.parse_args()
DATA = os.path.join(args.session, "depth_out")

# HSV colour rules (OpenCV H 0-180). red wraps.
COLORS = {
    "red":    [((0,120,50),(8,255,255)), ((170,120,50),(180,255,255))],
    "purple": [((135,80,40),(155,255,255))],
    "blue":   [((116,100,40),(132,255,255))],
    "green":  [((78,90,40),(114,255,255))],
}
DRAW = {"red":(0,0,255),"purple":(200,0,200),"blue":(255,0,0),"green":(0,180,0)}

fx,fy,cx,cy,W,H = open(os.path.join(DATA,"camera_info.txt")).read().split()
fx,fy,cx,cy = map(float,(fx,fy,cx,cy)); W,H = int(W),int(H)

frames={}
for ln in open(os.path.join(DATA,"stamps.txt")):
    p=ln.split()
    if len(p)==2: frames[int(p[0])]=float(p[1])
keys=sorted(frames)

ot=[];ox=[];oy=[];oth=[]
for ln in open(os.path.join(args.session,"odom.txt")):
    ln=ln.strip()
    if not ln or ln.startswith("#"): continue
    p=ln.split(); ot.append(float(p[0]));ox.append(float(p[1]));oy.append(float(p[2]));oth.append(float(p[3]))
ot=np.array(ot);ox=np.array(ox);oy=np.array(oy);oth=np.array(oth)

# static base_link -> camera_optical  (rpy = -pi/2, 0, -pi/2 ; translation fwd,0,up)
def rot_rpy(r,p,y):
    cr,sr=math.cos(r),math.sin(r); cp,sp=math.cos(p),math.sin(p); cy_,sy=math.cos(y),math.sin(y)
    Rx=np.array([[1,0,0],[0,cr,-sr],[0,sr,cr]])
    Ry=np.array([[cp,0,sp],[0,1,0],[-sp,0,cp]])
    Rz=np.array([[cy_,-sy,0],[sy,cy_,0],[0,0,1]])
    return Rz@Ry@Rx
R_bc=rot_rpy(-math.pi/2,0,-math.pi/2); t_bc=np.array([args.cam_fwd,0,args.cam_up])
T_bc=np.eye(4); T_bc[:3,:3]=R_bc; T_bc[:3,3]=t_bc
# sanity: camera forward (0,0,1) -> base
print("sanity cam-forward->base:", (R_bc@np.array([0,0,1])).round(2), "(應≈[1,0,0])")

def cam_pose_map(t):
    x=float(np.interp(t,ot,ox)); y=float(np.interp(t,ot,oy)); th=float(np.interp(t,ot,oth))
    T_mb=np.eye(4); T_mb[0,3]=x; T_mb[1,3]=y
    T_mb[:2,:2]=[[math.cos(th),-math.sin(th)],[math.sin(th),math.cos(th)]]
    return T_mb@T_bc

det={c:[] for c in COLORS}
for k in keys:
    bgr=cv2.imread(os.path.join(DATA,"rgb",f"{k:06d}.png"))
    dep=cv2.imread(os.path.join(DATA,"depth",f"{k:06d}.png"),cv2.IMREAD_UNCHANGED)
    if bgr is None or dep is None: continue
    hsv=cv2.cvtColor(bgr,cv2.COLOR_BGR2HSV)
    T_mc=cam_pose_map(frames[k])
    for c,ranges in COLORS.items():
        mask=np.zeros((H,W),np.uint8)
        for lo,hi in ranges: mask|=cv2.inRange(hsv,np.array(lo),np.array(hi))
        mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
        mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((7,7),np.uint8))
        n,lab,stats,cent=cv2.connectedComponentsWithStats(mask,8)
        for i in range(1,n):
            a=stats[i,cv2.CC_STAT_AREA]
            if a<120 or a>12000: continue
            w,h=stats[i,2],stats[i,3]
            if w>3*h or h>3*w: continue
            m=lab==i; dvals=dep[m]; dvals=dvals[dvals>0]
            if len(dvals)<20: continue
            d=np.median(dvals)/1000.0
            if d<0.25 or d>4.0: continue
            u,v=cent[i]
            Xc=(u-cx)/fx*d; Yc=(v-cy)/fy*d; Zc=d
            Pm=T_mc@np.array([Xc,Yc,Zc,1.0])
            if args.floor_min<=Pm[2]<=args.floor_max:
                det[c].append((Pm[0],Pm[1],Pm[2]))

print("\n=== 各色偵測與定位 (地圖座標, 公尺) ===")
result={}
for c in COLORS:
    pts=np.array(det[c])
    if len(pts)<3: print(f"  {c:7}: 偵測 {len(pts)} 次 (太少,不可靠)"); continue
    # robust center: median, then keep inliers within 0.4m, re-median
    med=np.median(pts[:,:2],axis=0)
    d=np.linalg.norm(pts[:,:2]-med,axis=1); inl=pts[d<0.4]
    ctr=np.median(inl[:,:2],axis=0) if len(inl)>=3 else med
    result[c]=(ctr[0],ctr[1])
    print(f"  {c:7}: {len(pts):4d} 偵測 -> (x={ctr[0]:+.2f}, y={ctr[1]:+.2f})  (inliers {len(inl)})")

# top-down plot: trajectory + blocks
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
plt.figure(figsize=(7,7))
plt.plot(ox,oy,'-',color='gray',lw=1,label='robot path')
for c,(x,y) in result.items():
    plt.scatter([x],[y],s=300,c=[np.array(DRAW[c][::-1])/255],edgecolors='k',zorder=5,label=c)
    plt.annotate(c,(x,y),fontsize=11,weight='bold')
plt.axis('equal'); plt.grid(True); plt.legend(); plt.title('blocks in map (wheel-odom frame)')
plt.xlabel('x (m)'); plt.ylabel('y (m)')
out=os.path.join(_ROS2, "utils/blocks_map.png"); plt.savefig(out,dpi=90); print("\nsaved",out)
# save waypoints
with open(os.path.join(_ROS2, "utils/block_waypoints.txt"),"w") as f:
    for c,(x,y) in result.items(): f.write(f"{c} {x:.3f} {y:.3f}\n")
print("saved <repo>/lekiwi_ros2/utils/block_waypoints.txt")
