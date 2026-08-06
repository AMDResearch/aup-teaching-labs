#!/usr/bin/env python3
"""Milestone 3: on-robot mapping recorder — teleop + wheel odometry + ZED stereo, one process.

Drive LeKiwi with the keyboard while it records raw stereo frames AND wheel odometry,
all timestamped together. Then Stage B (depth) + Stage C (RTAB-Map with wheel odom).

Keys:  W/S 前後  A/D 左右移  Z/X 左右轉  +/- 加減速  space 停  Q 結束
Output: rec/mapYYYYmmdd_HHMMSS/{frames/%06d.png, camera_stamps.txt, odom.txt}

Run (conda lerobot-new):
    python lekiwi_map_record.py --cam-dev 1 --port /dev/ttyACM0
"""
import os as _os  # repo-relative root (works from a git clone)
_ROS2 = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os, sys, time, math, select, termios, tty, threading, queue, datetime, argparse
import numpy as np, cv2
from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode
from lerobot.robots.lekiwi.lekiwi import LeKiwi

WHEELS = {"left_wheel": 7, "back_wheel": 8, "right_wheel": 9}
KEY_DIR = {"w":(1,0,0),"s":(-1,0,0),"a":(0,1,0),"d":(0,-1,0),"z":(0,0,1),"x":(0,0,-1)}
STOP_TIMEOUT = 0.25
W, H = 672, 376
BASE = os.path.join(_ROS2, "demo0/rec")

ap = argparse.ArgumentParser()
ap.add_argument("--cam-dev", type=int, default=1)
ap.add_argument("--port", default="/dev/ttyACM0")
ap.add_argument("--lin", type=float, default=0.12)
ap.add_argument("--ang", type=float, default=50.0)
args = ap.parse_args()

sess = os.path.join(BASE, datetime.datetime.now().strftime("map%Y%m%d_%H%M%S"))
os.makedirs(os.path.join(sess, "frames"), exist_ok=True)
print(f"錄製資料夾: {sess}")

# ---------- camera thread ----------
stop_evt = threading.Event()
cam_q = queue.Queue(maxsize=256)
cam_stamp_f = open(os.path.join(sess, "camera_stamps.txt"), "w")
cam_stamp_f.write(f"# W={W} H={H} side-by-side width {2*W}\n")

def cam_capture():
    cap = cv2.VideoCapture(args.cam_dev, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, W*2); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
    for _ in range(8): cap.read()
    idx = 0
    while not stop_evt.is_set():
        ok, fr = cap.read()
        if not ok or fr is None: continue
        t = time.time()
        try:
            cam_q.put_nowait((idx, fr.copy())); cam_stamp_f.write(f"{idx} {t:.6f}\n"); idx += 1
        except queue.Full:
            pass
    cap.release()
    print(f"\n[camera] {idx} 幀")

def cam_writer():
    while not (stop_evt.is_set() and cam_q.empty()):
        try: i, fr = cam_q.get(timeout=0.2)
        except queue.Empty: continue
        cv2.imwrite(os.path.join(sess, "frames", f"{i:06d}.png"), fr)

threading.Thread(target=cam_capture, daemon=True).start()
threading.Thread(target=cam_writer, daemon=True).start()

# ---------- motor bus ----------
bus = FeetechMotorsBus(port=args.port,
    motors={n: Motor(i, "sts3215", MotorNormMode.RANGE_M100_100) for n, i in WHEELS.items()})
print(f"連接馬達 {args.port} ...")
bus.connect()
bus.disable_torque(); bus.configure_motors()
for n in WHEELS: bus.write("Operating_Mode", n, OperatingMode.VELOCITY.value)
bus.enable_torque()

def send_body(x, y, th):
    w = LeKiwi._body_to_wheel_raw(LeKiwi, x, y, th)
    bus.sync_write("Goal_Velocity", {"left_wheel": w["base_left_wheel"],
                                     "back_wheel": w["base_back_wheel"],
                                     "right_wheel": w["base_right_wheel"]})
def stop_wheels():
    bus.sync_write("Goal_Velocity", dict.fromkeys(WHEELS, 0))

odom_f = open(os.path.join(sess, "odom.txt"), "w")
odom_f.write("# t x_m y_m theta_rad vx_mps vy_mps vtheta_radps  (wheel odometry, velocity-integrated)\n")

lin, ang = args.lin, args.ang
print("\n開車錄製: W前 S後 A左移 D右移 Z左轉 X右轉  +/-調速  space停  Q結束")
print(f"線速={lin:.2f} m/s 角速={ang:.0f} deg/s\n")

fd = sys.stdin.fileno(); old = termios.tcgetattr(fd)
x=y=th=0.0; last_key=0.0; cur=(0,0,0); moving=False; t_prev=time.time(); n_odom=0
try:
    tty.setcbreak(fd)
    while True:
        r,_,_ = select.select([sys.stdin],[],[],0.03)
        if r:
            ch = sys.stdin.read(1).lower()
            if ch == "q": break
            elif ch == " ": cur=(0,0,0); last_key=0.0
            elif ch in ("+","="): lin=min(lin+0.02,0.4); ang=min(ang+10,200); print(f"  速度 線={lin:.2f} 角={ang:.0f}    ")
            elif ch == "-": lin=max(lin-0.02,0.02); ang=max(ang-10,10); print(f"  速度 線={lin:.2f} 角={ang:.0f}    ")
            elif ch in KEY_DIR: cur=KEY_DIR[ch]; last_key=time.time()
        if last_key and (time.time()-last_key) > STOP_TIMEOUT: cur=(0,0,0); last_key=0.0
        dx,dy,dth = cur; xc,yc,thc = dx*lin, dy*lin, dth*ang
        if (xc,yc,thc)==(0,0,0):
            if moving: stop_wheels(); moving=False
        else:
            send_body(xc,yc,thc); moving=True
        # ---- wheel odometry (read present velocity -> body vel -> integrate) ----
        try:
            pv = bus.sync_read("Present_Velocity", list(WHEELS), normalize=False)
            bv = LeKiwi._wheel_raw_to_body(LeKiwi, pv["left_wheel"], pv["back_wheel"], pv["right_wheel"])
            vx, vy, vth = bv["x.vel"], bv["y.vel"], math.radians(bv["theta.vel"])
            now = time.time(); dt = now - t_prev; t_prev = now
            x += (vx*math.cos(th) - vy*math.sin(th))*dt
            y += (vx*math.sin(th) + vy*math.cos(th))*dt
            th += vth*dt
            odom_f.write(f"{now:.6f} {x:.4f} {y:.4f} {th:.5f} {vx:.4f} {vy:.4f} {vth:.5f}\n")
            n_odom += 1
            if n_odom % 30 == 0:
                print(f"\r  odom: x={x:+.2f} y={y:+.2f} th={math.degrees(th):+.0f}°  frames_q={cam_q.qsize()}   ", end="", flush=True)
        except Exception as e:
            pass
except KeyboardInterrupt:
    pass
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
    stop_wheels(); time.sleep(0.1); bus.disable_torque(); bus.disconnect()
    stop_evt.set(); time.sleep(0.5)
    cam_stamp_f.flush(); cam_stamp_f.close(); odom_f.flush(); odom_f.close()
    print(f"\n完成 -> {sess}\n  frames: {len(os.listdir(os.path.join(sess,'frames')))}  odom 行: {n_odom}")
    print(f"  最終里程計位置: x={x:.2f}m y={y:.2f}m theta={math.degrees(th):.0f}°")
