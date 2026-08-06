#!/usr/bin/env python3
"""Stamp the colored blocks as OCCUPIED cells on scene_map.pgm so Nav2 routes around them
(blocks are ~5cm tall, below the depth obstacle band, so they aren't in the map otherwise).
Run after block_localize.py. Re-run if blocks move / are re-localized.
"""
import os as _os  # repo-relative root (works from a git clone)
_ROS2 = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import cv2, yaml, os, math
O = os.path.join(_ROS2, "utils")
BLOCK_R = 0.06     # block half-size + margin (m); footprint marked occupied

m = yaml.safe_load(open(f"{O}/scene_map.yaml")); res = m["resolution"]; ox, oy = m["origin"][:2]
img = cv2.imread(f"{O}/scene_map.pgm", cv2.IMREAD_GRAYSCALE); H, W = img.shape
r = max(1, int(round(BLOCK_R / res)))
n = 0
for ln in open(f"{O}/block_waypoints.txt"):
    c, x, y = ln.split(); x, y = float(x), float(y)
    gx = int((x - ox) / res); gy = int((y - oy) / res); py = H - 1 - gy
    cv2.rectangle(img, (gx - r, py - r), (gx + r, py + r), 0, -1)   # 0 = occupied
    print(f"  marked {c} at ({x:+.2f},{y:+.2f}) -> cell ({gx},{gy})"); n += 1
cv2.imwrite(f"{O}/scene_map.pgm", img)
cv2.imwrite(f"{O}/scene_map_preview.png", cv2.resize(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), (W*5, H*5), interpolation=cv2.INTER_NEAREST))
print(f"stamped {n} blocks as obstacles (r={r} cells ~{r*res*100:.0f}cm) into scene_map.pgm")
