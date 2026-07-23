#!/usr/bin/env python3
"""Milestone 3 GUI recorder: live preview + drive (WASD) + wheel odometry + record, one window.

Keys (window must be focused):
  W/S 前後  A/D 左右移  Z/X 左右轉  space 停  +/- 加減速
Buttons: 開始錄製 / 停止 / 離開
Records rec/mapYYYYmmdd_HHMMSS/{frames/%06d.png, camera_stamps.txt, odom.txt}

Run (conda lerobot-new):
  PYTHONNOUSERSITE=1 python lekiwi_map_gui.py --cam-dev 1
"""
import os as _os  # repo-relative root (works from a git clone)
_ROS2 = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os, time, math, threading, queue, datetime, argparse
import numpy as np, cv2
import tkinter as tk
from PIL import Image, ImageTk
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
ap.add_argument("--lin", type=float, default=0.08)
ap.add_argument("--ang", type=float, default=30.0)
args = ap.parse_args()


class MapGUI:
    def __init__(self, root):
        self.root = root; root.title("LeKiwi 建圖錄製器")
        self.lin, self.ang = args.lin, args.ang
        self.cur = (0, 0, 0); self.last_key = 0.0
        self.recording = False; self.run = True
        self.latest = None; self.lock = threading.Lock()
        self.sess = None; self.cam_q = queue.Queue(maxsize=256)
        self.cam_stamp = None; self.odom_f = None
        self.cam_idx = 0
        self.x = self.y = self.th = 0.0; self.n_odom = 0; self.t_prev = time.time()

        # ---- motor bus ----
        self.bus = FeetechMotorsBus(port=args.port,
            motors={n: Motor(i, "sts3215", MotorNormMode.RANGE_M100_100) for n, i in WHEELS.items()})
        self.bus.connect(); self.bus.disable_torque(); self.bus.configure_motors()
        for n in WHEELS: self.bus.write("Operating_Mode", n, OperatingMode.VELOCITY.value)
        self.bus.enable_torque()

        # ---- UI ----
        self.view = tk.Label(root, bg="black"); self.view.pack(padx=6, pady=6)
        bar = tk.Frame(root); bar.pack(fill="x", padx=6)
        self.b_start = tk.Button(bar, text="● 開始錄製", command=self.start, bg="#2e7d32", fg="white", font=("Sans",14,"bold"), width=11)
        self.b_stop = tk.Button(bar, text="■ 停止", command=self.stop, state="disabled", bg="#c62828", fg="white", font=("Sans",14,"bold"), width=9)
        self.b_quit = tk.Button(bar, text="離開", command=self.quit, font=("Sans",12), width=7)
        self.b_start.pack(side="left", padx=3); self.b_stop.pack(side="left", padx=3); self.b_quit.pack(side="right", padx=3)
        self.status = tk.Label(root, text="就緒 — W/S/A/D/Z/X 開車, +/- 調速", font=("Sans",12), anchor="w"); self.status.pack(fill="x", padx=8, pady=6)

        for k in list(KEY_DIR)+["space","plus","minus","equal"]:
            root.bind(f"<KeyPress-{k}>", self.on_key)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.focus_set()

        threading.Thread(target=self.cam_loop, daemon=True).start()
        threading.Thread(target=self.cam_writer, daemon=True).start()
        threading.Thread(target=self.motor_loop, daemon=True).start()
        self.tick()

    def on_key(self, e):
        k = e.keysym.lower()
        if k == "space": self.cur = (0,0,0); self.last_key = 0.0
        elif k in ("plus","equal"): self.lin=min(self.lin+0.02,0.4); self.ang=min(self.ang+10,200)
        elif k == "minus": self.lin=max(self.lin-0.02,0.02); self.ang=max(self.ang-10,10)
        elif k in KEY_DIR: self.cur = KEY_DIR[k]; self.last_key = time.time()

    def cam_loop(self):
        cap = cv2.VideoCapture(args.cam_dev, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, W*2); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
        for _ in range(8): cap.read()
        while self.run:
            ok, fr = cap.read()
            if not ok or fr is None: continue
            with self.lock: self.latest = fr
            if self.recording and self.cam_stamp is not None:
                t = time.time()
                try: self.cam_q.put_nowait((self.cam_idx, fr[:, :W].copy() if False else fr.copy())); self.cam_stamp.write(f"{self.cam_idx} {t:.6f}\n"); self.cam_idx += 1
                except queue.Full: pass
        cap.release()

    def cam_writer(self):
        while self.run or not self.cam_q.empty():
            try: i, fr = self.cam_q.get(timeout=0.2)
            except queue.Empty: continue
            if self.sess: cv2.imwrite(os.path.join(self.sess,"frames",f"{i:06d}.png"), fr)

    def motor_loop(self):
        while self.run:
            dx,dy,dth = self.cur
            if self.last_key and (time.time()-self.last_key) > STOP_TIMEOUT: self.cur=(0,0,0); dx=dy=dth=0
            xc,yc,thc = dx*self.lin, dy*self.lin, dth*self.ang
            try:
                if (xc,yc,thc)==(0,0,0):
                    self.bus.sync_write("Goal_Velocity", dict.fromkeys(WHEELS,0))
                else:
                    w = LeKiwi._body_to_wheel_raw(LeKiwi, xc,yc,thc)
                    self.bus.sync_write("Goal_Velocity", {"left_wheel":w["base_left_wheel"],"back_wheel":w["base_back_wheel"],"right_wheel":w["base_right_wheel"]})
                pv = self.bus.sync_read("Present_Velocity", list(WHEELS), normalize=False)
                db = 12  # deadband: ignore idle encoder noise to reduce odom drift
                lw = 0 if abs(pv["left_wheel"])<db else pv["left_wheel"]
                bw = 0 if abs(pv["back_wheel"])<db else pv["back_wheel"]
                rw = 0 if abs(pv["right_wheel"])<db else pv["right_wheel"]
                bv = LeKiwi._wheel_raw_to_body(LeKiwi, lw, bw, rw)
                vx,vy,vth = bv["x.vel"], bv["y.vel"], math.radians(bv["theta.vel"])
                now=time.time(); dt=now-self.t_prev; self.t_prev=now
                if self.recording:
                    self.x += (vx*math.cos(self.th)-vy*math.sin(self.th))*dt
                    self.y += (vx*math.sin(self.th)+vy*math.cos(self.th))*dt
                    self.th += vth*dt
                    if self.odom_f: self.odom_f.write(f"{now:.6f} {self.x:.4f} {self.y:.4f} {self.th:.5f} {vx:.4f} {vy:.4f} {vth:.5f}\n"); self.n_odom+=1
            except Exception: pass
            time.sleep(0.03)

    def start(self):
        if self.recording: return
        self.sess = os.path.join(BASE, datetime.datetime.now().strftime("map%Y%m%d_%H%M%S"))
        os.makedirs(os.path.join(self.sess,"frames"), exist_ok=True)
        self.cam_stamp = open(os.path.join(self.sess,"camera_stamps.txt"),"w"); self.cam_stamp.write(f"# W={W} H={H} width {2*W}\n")
        self.odom_f = open(os.path.join(self.sess,"odom.txt"),"w"); self.odom_f.write("# t x_m y_m theta_rad vx vy vtheta_radps\n")
        self.cam_idx=0; self.x=self.y=self.th=0.0; self.n_odom=0
        self.recording=True; self.b_start.configure(state="disabled"); self.b_stop.configure(state="normal")

    def stop(self):
        if not self.recording: return
        self.recording=False; time.sleep(0.1)
        if self.cam_stamp: self.cam_stamp.flush(); self.cam_stamp.close(); self.cam_stamp=None
        if self.odom_f: self.odom_f.flush(); self.odom_f.close(); self.odom_f=None
        self.b_start.configure(state="normal"); self.b_stop.configure(state="disabled")
        self.status.configure(text=f"已停止: {self.cam_idx} 幀, odom {self.n_odom} 行 -> {self.sess}", fg="#1565c0")

    def quit(self):
        self.run=False
        if self.recording: self.stop()
        time.sleep(0.2)
        try: self.bus.sync_write("Goal_Velocity", dict.fromkeys(WHEELS,0)); self.bus.disable_torque(); self.bus.disconnect()
        except Exception: pass
        self.root.destroy()

    def tick(self):
        with self.lock: f = None if self.latest is None else self.latest[:, :W].copy()
        if f is not None:
            img = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)))
            self.view.configure(image=img); self.view.image = img
        drive = "動" if self.cur!=(0,0,0) else "停"
        if self.recording:
            self.status.configure(text=f"● 錄製 {self.cam_idx}幀  odom x={self.x:+.2f} y={self.y:+.2f} th={math.degrees(self.th):+.0f}°  速{self.lin:.2f}m/s [{drive}]", fg="#c62828")
        else:
            self.status.configure(text=f"就緒 [{drive}]  線速{self.lin:.2f}m/s 角速{self.ang:.0f}°/s — 按開始錄製", fg="black")
        self.root.after(50, self.tick)


if __name__ == "__main__":
    root = tk.Tk(); MapGUI(root); root.mainloop()
