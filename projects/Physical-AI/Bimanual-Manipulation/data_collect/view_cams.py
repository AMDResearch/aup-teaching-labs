#!/usr/bin/env python
"""Live view of the head + wrist cameras, side by side in one OpenCV window.

Deliberately not rerun: its viewer cannot keep up with two 640x480 streams on
this machine, and once its channel fills the SDK blocks the caller for seconds at
a time. cv2.imshow draws both frames in well under a millisecond.

Needs the GUI build of OpenCV. lerobot pins opencv-python-headless, whose imshow
raises "The function is not implemented" -- see SETUP.md for the fix.

Both cameras sit on the same USB 2.0 hub, so MJPG is the default: uncompressed
YUYV at 640x480x30 is ~147 Mbps per camera and two of those do not fit.

Usage:
    python view_cams.py                       # 640x480 @ 30fps, MJPG
    python view_cams.py --width 320 --height 240   # lighter, e.g. over SSH
    python view_cams.py --cam head            # one camera only
"""

import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from lerobot.cameras.opencv import OpenCVCamera, OpenCVCameraConfig

# by-path, not by-id: both cameras are the same model with an identical serial
# string, so by-id collapses them into one symlink. See SETUP.md.
BY_PATH = "/dev/v4l/by-path"
CAMERAS = {
    "head": f"{BY_PATH}/pci-0000:bf:00.4-usb-0:1:1.0-video-index0",
    "wrist": f"{BY_PATH}/pci-0000:bd:00.4-usb-0:1.3:1.0-video-index0",
}
WINDOW = "xlerobot cameras"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fourcc", type=str, default="MJPG")
    parser.add_argument("--cam", type=str, default=None, choices=list(CAMERAS))
    args = parser.parse_args()

    selected = {args.cam: CAMERAS[args.cam]} if args.cam else CAMERAS

    cameras = {}
    for name, path in selected.items():
        if not Path(path).exists():
            raise SystemExit(f"missing {path}\n  list them with: ls {BY_PATH}/*-usb-*-video-index0")
        cam = OpenCVCamera(OpenCVCameraConfig(
            index_or_path=Path(path), fps=args.fps, width=args.width, height=args.height,
            fourcc=args.fourcc,
        ))
        cam.connect()
        cameras[name] = cam
        print(f"connected {name} -> {path}")

    print(f"streaming {args.width}x{args.height}@{args.fps} {args.fourcc}... q or ctrl-c to stop")

    counts = dict.fromkeys(cameras, 0)
    drops = dict.fromkeys(cameras, 0)
    last_report = time.perf_counter()

    try:
        while True:
            tiles = []
            for name, cam in cameras.items():
                try:
                    # A short timeout keeps one stalled camera from pacing the loop;
                    # the default is 200 ms, longer than a frame interval.
                    frame = cam.async_read(timeout_ms=60)
                    counts[name] += 1
                except (OSError, RuntimeError):
                    drops[name] += 1
                    continue
                tile = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                cv2.putText(tile, name, (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
                tiles.append(tile)

            if tiles:
                height = min(t.shape[0] for t in tiles)
                cv2.imshow(WINDOW, np.hstack([t[:height] for t in tiles]))
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            now = time.perf_counter()
            if now - last_report >= 2.0:
                print("  ".join(
                    f"{n}: {counts[n] / (now - last_report):5.1f} fps ({drops[n]} drops)"
                    for n in cameras
                ))
                counts = dict.fromkeys(cameras, 0)
                drops = dict.fromkeys(cameras, 0)
                last_report = now
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        cv2.destroyAllWindows()
        for cam in cameras.values():
            cam.disconnect()


if __name__ == "__main__":
    main()
