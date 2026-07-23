#!/usr/bin/env python3
"""Stage C (wheel-odom): replay offline RGB-D + wheel odometry for RTAB-Map.
Publishes per frame (at recorded camera stamp):
    /zed/rgb/image (rgb8), /zed/depth/image (16UC1 mm), /zed/rgb/camera_info
    /odom (nav_msgs/Odometry, odom->base_link)  + TF odom->base_link
Wheel odometry is interpolated from odom.txt to each frame's timestamp.

Run (conda lerobot-new, ROS sourced):
    python stage_c_replay_odom.py --data <session>/depth_out --odom <session>/odom.txt --rate 3
"""
import os, argparse, math, numpy as np, cv2
import rclpy
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import Image, CameraInfo
from nav_msgs.msg import Odometry
from geometry_msgs.msg import TransformStamped
from tf2_ros import TransformBroadcaster
from std_msgs.msg import Header

ap = argparse.ArgumentParser()
ap.add_argument("--data", required=True)
ap.add_argument("--odom", required=True)
ap.add_argument("--rate", type=float, default=3.0)
ap.add_argument("--ns", default="/zed")
ap.add_argument("--optical-frame", default="camera_optical_frame")
ap.add_argument("--base-frame", default="base_link")
ap.add_argument("--odom-frame", default="odom")
args = ap.parse_args()

fx,fy,cx,cy,W,H = open(os.path.join(args.data,"camera_info.txt")).read().split()
fx,fy,cx,cy = map(float,(fx,fy,cx,cy)); W,H = int(W),int(H)

# frames
frames = {}
with open(os.path.join(args.data,"stamps.txt")) as f:
    for ln in f:
        p=ln.split()
        if len(p)==2: frames[int(p[0])]=float(p[1])
keys = sorted(frames)

# odom trajectory
ot=[]; ox=[]; oy=[]; oth=[]
with open(args.odom) as f:
    for ln in f:
        ln=ln.strip()
        if not ln or ln.startswith("#"): continue
        p=ln.split(); ot.append(float(p[0])); ox.append(float(p[1])); oy.append(float(p[2])); oth.append(float(p[3]))
ot=np.array(ot); ox=np.array(ox); oy=np.array(oy); oth=np.array(oth)
print(f"{len(keys)} frames, {len(ot)} odom samples, publishing at {args.rate} fps")

def interp_odom(t):
    return float(np.interp(t,ot,ox)), float(np.interp(t,ot,oy)), float(np.interp(t,ot,oth))


class ReplayOdom(Node):
    def __init__(self):
        super().__init__("stage_c_replay_odom")
        ns=args.ns.rstrip("/")
        self.p_rgb=self.create_publisher(Image,f"{ns}/rgb/image",2)
        self.p_dep=self.create_publisher(Image,f"{ns}/depth/image",2)
        self.p_ci =self.create_publisher(CameraInfo,f"{ns}/rgb/camera_info",2)
        self.p_odom=self.create_publisher(Odometry,"/odom",10)
        self.tfb=TransformBroadcaster(self)
        self.ci=CameraInfo(); self.ci.width=W; self.ci.height=H; self.ci.distortion_model="plumb_bob"; self.ci.d=[0.0]*5
        self.ci.k=[fx,0.0,cx,0.0,fy,cy,0.0,0.0,1.0]; self.ci.r=[1.0,0,0,0,1.0,0,0,0,1.0]
        self.ci.p=[fx,0.0,cx,0.0,0.0,fy,cy,0.0,0.0,0.0,1.0,0.0]
        self.i=0
        self.timer=self.create_timer(1.0/args.rate,self.tick)

    def img(self,stamp,arr,enc,step,frame):
        m=Image(); m.header=Header(); m.header.stamp=stamp; m.header.frame_id=frame
        m.height,m.width=arr.shape[0],arr.shape[1]; m.encoding=enc; m.is_bigendian=0; m.step=step; m.data=arr.tobytes(); return m

    def tick(self):
        if self.i>=len(keys):
            self.get_logger().info("replay finished; keeping alive for RTAB-Map (Ctrl-C to quit)"); self.timer.cancel(); return
        k=keys[self.i]; self.i+=1; ts=frames[k]
        sec=int(ts); nsec=int((ts-sec)*1e9); stamp=Time(seconds=sec,nanoseconds=nsec).to_msg()
        x,y,th=interp_odom(ts); qz=math.sin(th/2); qw=math.cos(th/2)
        # TF odom->base_link
        tf=TransformStamped(); tf.header.stamp=stamp; tf.header.frame_id=args.odom_frame; tf.child_frame_id=args.base_frame
        tf.transform.translation.x=x; tf.transform.translation.y=y; tf.transform.translation.z=0.0
        tf.transform.rotation.z=qz; tf.transform.rotation.w=qw
        self.tfb.sendTransform(tf)
        # /odom
        od=Odometry(); od.header.stamp=stamp; od.header.frame_id=args.odom_frame; od.child_frame_id=args.base_frame
        od.pose.pose.position.x=x; od.pose.pose.position.y=y; od.pose.pose.orientation.z=qz; od.pose.pose.orientation.w=qw
        self.p_odom.publish(od)
        # RGB-D
        bgr=cv2.imread(os.path.join(args.data,"rgb",f"{k:06d}.png"))
        dep=cv2.imread(os.path.join(args.data,"depth",f"{k:06d}.png"),cv2.IMREAD_UNCHANGED)
        if bgr is None or dep is None: return
        rgb=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
        self.ci.header.stamp=stamp; self.ci.header.frame_id=args.optical_frame
        self.p_rgb.publish(self.img(stamp,rgb,"rgb8",W*3,args.optical_frame))
        self.p_dep.publish(self.img(stamp,dep.astype(np.uint16),"16UC1",W*2,args.optical_frame))
        self.p_ci.publish(self.ci)
        if self.i%20==0: self.get_logger().info(f"published {self.i}/{len(keys)}  odom=({x:.2f},{y:.2f},{math.degrees(th):.0f}°)")


def main():
    rclpy.init(); n=ReplayOdom()
    try: rclpy.spin(n)
    except KeyboardInterrupt: pass
    finally: n.destroy_node(); rclpy.shutdown()

if __name__=="__main__": main()
