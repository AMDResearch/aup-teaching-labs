#!/usr/bin/env python3
import os as _os  # repo-relative root (works from a git clone)
_ROS2 = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import sys, numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

path = sys.argv[1] if len(sys.argv)>1 else "/home/aup/.ros/scene_cloud.ply"
out  = sys.argv[2] if len(sys.argv)>2 else "<repo>/lekiwi_ros2/utils/scene_cloud_preview.png"

# --- parse PLY header ---
with open(path,"rb") as f:
    assert f.readline().strip()==b"ply"
    fmt=f.readline().strip().decode()
    n=0; props=[]
    typemap={"float":"f4","float32":"f4","double":"f8","uchar":"u1","uint8":"u1",
             "int":"i4","short":"i2","ushort":"u2"}
    cur=None
    while True:
        line=f.readline().strip().decode()
        if line.startswith("element"):
            parts=line.split(); cur=parts[1]
            if cur=="vertex": n=int(parts[-1])
        elif line.startswith("property") and cur=="vertex":
            _,t,name=line.split(); props.append((name,typemap[t]))
        elif line=="end_header": break
    endian="<" if "little" in fmt else ">"
    dt=np.dtype([(nm,endian+tp) for nm,tp in props])
    data=np.frombuffer(f.read(n*dt.itemsize), dtype=dt, count=n)

xyz=np.vstack([data["x"],data["y"],data["z"]]).T
rgb=np.vstack([data["red"],data["green"],data["blue"]]).T/255.0
print(f"{n} points, bounds x[{xyz[:,0].min():.1f},{xyz[:,0].max():.1f}] "
      f"y[{xyz[:,1].min():.1f},{xyz[:,1].max():.1f}] z[{xyz[:,2].min():.1f},{xyz[:,2].max():.1f}]")

# subsample for plotting
if n>40000:
    idx=np.random.choice(n,40000,replace=False); xyz=xyz[idx]; rgb=rgb[idx]

fig=plt.figure(figsize=(16,7))
ax=fig.add_subplot(121,projection="3d")
ax.scatter(xyz[:,0],xyz[:,1],xyz[:,2],c=rgb,s=1,marker=".")
ax.set_title("perspective"); ax.set_xlabel("x"); ax.set_ylabel("y"); ax.set_zlabel("z")
ax.view_init(elev=-70, azim=-90)
try: ax.set_box_aspect((np.ptp(xyz[:,0]),np.ptp(xyz[:,1]),np.ptp(xyz[:,2])))
except Exception: pass

ax2=fig.add_subplot(122)
ax2.scatter(xyz[:,0],xyz[:,1],c=rgb,s=1,marker=".")
ax2.set_title("top-down (x-y)"); ax2.set_xlabel("x"); ax2.set_ylabel("y"); ax2.set_aspect("equal")
plt.tight_layout(); plt.savefig(out,dpi=90); print("saved",out)
