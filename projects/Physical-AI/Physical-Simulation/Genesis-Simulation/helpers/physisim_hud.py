# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""OpenCV HUD and recording helpers for GS05 and GS06.

The module is intentionally independent of Genesis and the planner. Notebooks
pass ordinary NumPy images and structured receipts into :func:`compose_hud_frame`.
"""

from __future__ import annotations

import collections
import copy
import json
import os
import shutil
import subprocess
import textwrap
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np

COMP_W = 1440
COMP_H = 816
MAIN_W = 820
PANEL_W = COMP_W - MAIN_W

THEME = {
    "background": (18, 15, 16),
    "panel": (34, 29, 30),
    "panel_alt": (45, 38, 39),
    "border": (80, 68, 70),
    "text": (235, 235, 235),
    "muted": (165, 155, 158),
    "amd_red": (34, 34, 220),
    "orange": (0, 155, 255),
    "green": (105, 220, 80),
    "blue": (235, 155, 70),
    "danger": (80, 80, 235),
}


def _as_uint8_rgb(image: Any) -> np.ndarray:
    arr = np.asarray(image)
    if arr.ndim == 4 and arr.shape[0] == 1:
        arr = arr[0]
    if arr.ndim == 2:
        arr = np.repeat(arr[..., None], 3, axis=-1)
    if arr.shape[-1] > 3:
        arr = arr[..., :3]
    if arr.dtype != np.uint8:
        arr = arr.astype(np.float32)
        if arr.size and float(np.nanmax(arr)) <= 1.5:
            arr *= 255.0
        arr = np.nan_to_num(arr, nan=0.0, posinf=255.0, neginf=0.0)
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(arr)


def _fit_bgr(image: np.ndarray, width: int, height: int) -> np.ndarray:
    """Fit a BGR image without changing its aspect ratio."""

    source_h, source_w = image.shape[:2]
    scale = min(width / source_w, height / source_h)
    resized_w = max(1, int(source_w * scale))
    resized_h = max(1, int(source_h * scale))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    resized = cv2.resize(image, (resized_w, resized_h), interpolation=interpolation)
    canvas = np.full((height, width, 3), THEME["background"], dtype=np.uint8)
    x = (width - resized_w) // 2
    y = (height - resized_h) // 2
    canvas[y : y + resized_h, x : x + resized_w] = resized
    return canvas


def _letterbox_bgr(rgb: Any, width: int, height: int) -> np.ndarray:
    image = cv2.cvtColor(_as_uint8_rgb(rgb), cv2.COLOR_RGB2BGR)
    return _fit_bgr(image, width, height)


def _put(
    image: np.ndarray,
    text: str,
    x: int,
    y: int,
    scale: float = 0.45,
    color: tuple[int, int, int] | None = None,
    thickness: int = 1,
) -> None:
    cv2.putText(
        image,
        str(text),
        (int(x), int(y)),
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color or THEME["text"],
        thickness,
        cv2.LINE_AA,
    )


def _panel(
    image: np.ndarray,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    label: str | None = None,
    accent: tuple[int, int, int] | None = None,
) -> None:
    cv2.rectangle(image, (x0, y0), (x1, y1), THEME["panel"], -1)
    cv2.rectangle(image, (x0, y0), (x1, y1), THEME["border"], 1)
    if label:
        cv2.rectangle(image, (x0, y0), (x0 + 4, y1), accent or THEME["orange"], -1)
        _put(image, label.upper(), x0 + 12, y0 + 20, 0.42, THEME["muted"])


def _bar(
    image: np.ndarray,
    x: int,
    y: int,
    width: int,
    pct: float,
    label: str,
    color: tuple[int, int, int],
) -> None:
    pct = float(np.clip(pct, 0, 100))
    _put(image, f"{label} {pct:5.1f}%", x, y - 5, 0.38, THEME["text"])
    cv2.rectangle(image, (x, y), (x + width, y + 9), THEME["panel_alt"], -1)
    cv2.rectangle(image, (x, y), (x + int(width * pct / 100.0), y + 9), color, -1)


def _sparkline(
    image: np.ndarray,
    values: Sequence[float],
    x: int,
    y: int,
    width: int,
    height: int,
    color: tuple[int, int, int],
) -> None:
    if len(values) < 2:
        return
    vals = np.clip(np.asarray(values, dtype=np.float32), 0, 100)
    xs = np.linspace(x, x + width, vals.size)
    ys = y + height - vals / 100.0 * height
    points = np.stack([xs, ys], axis=-1).astype(np.int32)
    cv2.polylines(image, [points], False, color, 1, cv2.LINE_AA)


def _truncate(value: Any, limit: int) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except TypeError:
            text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _put_wrapped(
    image: np.ndarray,
    value: Any,
    x: int,
    y: int,
    *,
    width: int,
    max_lines: int,
    scale: float = 0.40,
    line_height: int = 18,
    color: tuple[int, int, int] | None = None,
    thickness: int = 1,
) -> None:
    """Render compact wrapped text inside a fixed-width HUD region."""

    text = _truncate(value, max(width * max_lines * 2, width))
    lines = textwrap.wrap(
        text,
        width=width,
        break_long_words=True,
        break_on_hyphens=False,
    ) or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _truncate(lines[-1], max(1, width - 1))
    for index, line in enumerate(lines):
        _put(
            image,
            line,
            x,
            y + index * line_height,
            scale,
            color,
            thickness,
        )


def _image_thumbnail(key: str, value: Any, width: int, height: int) -> np.ndarray:
    arr = np.asarray(value)
    if arr.ndim == 4 and arr.shape[0] == 1:
        arr = arr[0]
    if arr.ndim == 3 and arr.shape[-1] == 1:
        arr = arr[..., 0]
    if arr.ndim == 2:
        gray = arr
        if gray.dtype != np.uint8:
            gray = np.nan_to_num(gray.astype(np.float32))
            if gray.size and float(gray.max()) <= 1.5:
                gray *= 255.0
            gray = np.clip(gray, 0, 255).astype(np.uint8)
        cmap = cv2.COLORMAP_INFERNO if key in {"depth", "sobel"} else cv2.COLORMAP_BONE
        bgr = cv2.applyColorMap(gray, cmap)
    else:
        rgb = _as_uint8_rgb(arr)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return _fit_bgr(bgr, width, height)


def _tactile_thumbnail(displacement: Any, width: int, height: int) -> np.ndarray:
    if displacement is None:
        return np.zeros((height, width, 3), dtype=np.uint8)
    arr = np.asarray(displacement, dtype=np.float32)
    if arr.ndim == 4 and arr.shape[0] == 1:
        arr = arr[0]
    magnitude = np.linalg.norm(arr, axis=-1)
    peak = float(magnitude.max()) if magnitude.size else 0.0
    scaled = magnitude / max(peak, 1e-9) * 255.0
    heatmap = cv2.applyColorMap(scaled.astype(np.uint8), cv2.COLORMAP_MAGMA)
    return cv2.resize(heatmap, (width, height), interpolation=cv2.INTER_NEAREST)


def build_vision_thumbnails(
    rgb: Any,
    depth: Any,
    segmentation: Any,
    normal: Any,
) -> dict[str, np.ndarray]:
    """Build current CPU-side HUD thumbnails from one Genesis render."""

    rgb_u8 = _as_uint8_rgb(rgb)
    depth_arr = np.asarray(depth, dtype=np.float32).squeeze()
    seg_ids = np.asarray(segmentation, dtype=np.int32).squeeze()
    normal_arr = np.asarray(normal).squeeze()

    valid = np.isfinite(depth_arr) & (depth_arr > 0)
    depth_u8 = np.zeros(depth_arr.shape, dtype=np.uint8)
    if valid.any():
        near, far = np.quantile(depth_arr[valid], (0.02, 0.98))
        scaled = np.clip((depth_arr - near) / max(float(far - near), 1e-6), 0, 1)
        depth_u8[valid] = (scaled[valid] * 255).astype(np.uint8)

    if normal_arr.dtype == np.uint8 or (normal_arr.size and float(normal_arr.max()) > 1.5):
        normal_u8 = np.clip(normal_arr, 0, 255).astype(np.uint8)
    elif normal_arr.size and float(normal_arr.min()) < -0.05:
        normal_u8 = np.clip((normal_arr + 1.0) * 127.5, 0, 255).astype(np.uint8)
    else:
        normal_u8 = np.clip(normal_arr * 255.0, 0, 255).astype(np.uint8)
    normal_u8[seg_ids == 0] = 0

    segmentation_u8 = np.stack(
        [
            (seg_ids * 37 + 11) % 256,
            (seg_ids * 79 + 43) % 256,
            (seg_ids * 131 + 97) % 256,
        ],
        axis=-1,
    ).astype(np.uint8)
    segmentation_u8[seg_ids == 0] = 0

    gray = cv2.cvtColor(rgb_u8, cv2.COLOR_RGB2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 1.4)
    edge_x = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
    edge_y = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = cv2.magnitude(edge_x, edge_y)
    sobel = np.clip(magnitude / max(float(magnitude.max()), 1e-6) * 255, 0, 255).astype(np.uint8)

    return {
        "depth": depth_u8,
        "normal": normal_u8,
        "segmentation": segmentation_u8,
        "gray": gray,
        "blur": blur,
        "sobel": sobel,
    }


def _render_main(
    scene_rgb: Any,
    status: str,
    tactile: Mapping[str, Any] | None,
    title: str,
) -> np.ndarray:
    canvas = np.full((COMP_H, MAIN_W, 3), THEME["background"], dtype=np.uint8)
    cv2.rectangle(canvas, (0, 0), (MAIN_W, 64), THEME["panel"], -1)
    cv2.rectangle(canvas, (0, 0), (12, 64), THEME["amd_red"], -1)
    _put(canvas, title, 28, 31, 0.72, THEME["text"], 2)
    _put(canvas, "Genesis 1.3.1 · AMD ROCm", 29, 54, 0.42, THEME["muted"])
    _put(canvas, time.strftime("%H:%M:%S"), MAIN_W - 112, 38, 0.48, THEME["muted"])

    scene_y = 76
    scene_h = COMP_H - 154
    scene = _letterbox_bgr(scene_rgb, MAIN_W - 32, scene_h)
    canvas[scene_y : scene_y + scene_h, 16 : 16 + scene.shape[1]] = scene
    cv2.rectangle(
        canvas,
        (15, scene_y - 1),
        (MAIN_W - 16, scene_y + scene_h),
        THEME["border"],
        1,
    )

    tactile = dict(tactile or {})
    secure = bool(tactile.get("secure", False))
    contact = int(tactile.get("n_contact", 0))
    total = int(tactile.get("n_taxels", 128))
    force = float(tactile.get("grip_force_N", 0.0))
    banner_color = THEME["green"] if secure else THEME["orange"]
    banner = "GRIP SECURE" if secure else "TACTILE MONITORING"
    cv2.rectangle(canvas, (28, 90), (390, 134), THEME["panel"], -1)
    cv2.rectangle(canvas, (28, 90), (34, 134), banner_color, -1)
    _put(canvas, banner, 45, 110, 0.48, banner_color, 2)
    _put(
        canvas,
        f"K4  {force:.2f} N   contact {contact}/{total}",
        45,
        128,
        0.40,
        THEME["text"],
    )

    cv2.rectangle(canvas, (16, COMP_H - 54), (MAIN_W - 16, COMP_H - 12), THEME["panel"], -1)
    _put(canvas, _truncate(status or "Ready", 90), 29, COMP_H - 27, 0.52, THEME["text"], 1)
    return canvas


def _render_brain(
    gpu: Mapping[str, Any] | None,
    user_input: str,
    planner_raw: Any,
    plan: Any,
    stages: Sequence[Any] | None,
    thumbnails: Mapping[str, Any] | None,
    tactile: Mapping[str, Any] | None,
    left_tactile: Any,
    right_tactile: Any,
) -> np.ndarray:
    panel = np.full((COMP_H, PANEL_W, 3), THEME["background"], dtype=np.uint8)
    _put(panel, "AI BRAIN", 18, 32, 0.78, THEME["text"], 2)
    _put(panel, "PLAN · PERCEPTION · ACTION · VERIFY", 19, 52, 0.38, THEME["muted"])

    gpu = dict(gpu or {})
    _panel(panel, 14, 62, PANEL_W - 14, 150, "AMD GPU telemetry", THEME["amd_red"])
    gpu_pct = float(gpu.get("gpu_pct", 0.0))
    vram_pct = float(gpu.get("vram_pct", 0.0))
    bar_w = 245
    _bar(panel, 30, 98, bar_w, gpu_pct, "GPU", THEME["amd_red"])
    _bar(panel, 330, 98, bar_w, vram_pct, "VRAM", THEME["blue"])
    _sparkline(panel, gpu.get("gpu_history", []), 30, 116, bar_w, 22, THEME["amd_red"])
    _sparkline(panel, gpu.get("vram_history", []), 330, 116, bar_w, 22, THEME["blue"])
    _put(panel, f"{float(gpu.get('temp_c', 0.0)):.0f} C", PANEL_W - 70, 84, 0.42, THEME["muted"])
    _put(panel, _truncate(gpu.get("source", "not available"), 66), 30, 144, 0.34, THEME["muted"])

    _panel(panel, 14, 158, PANEL_W - 14, 238, "User input / planner", THEME["orange"])
    _put_wrapped(
        panel,
        "> " + _truncate(user_input or "(not provided)", 88),
        30,
        190,
        width=82,
        max_lines=1,
        scale=0.46,
        color=THEME["text"],
        thickness=1,
    )
    _put_wrapped(
        panel,
        planner_raw or "(offline / no raw model output)",
        30,
        216,
        width=94,
        max_lines=1,
        scale=0.38,
        color=THEME["muted"],
    )

    _panel(panel, 14, 246, PANEL_W - 14, 334, "Validated plan / execution", THEME["green"])
    _put_wrapped(
        panel,
        plan or "(no plan)",
        30,
        278,
        width=92,
        max_lines=2,
        scale=0.40,
        line_height=18,
        color=THEME["text"],
    )
    stage_lines = []
    for stage in list(stages or [])[-2:]:
        if isinstance(stage, Mapping):
            label = stage.get("stage", "stage")
            ok = stage.get("success", stage.get("result", {}).get("success", ""))
            stage_lines.append(f"{label}: {ok}")
        else:
            stage_lines.append(str(stage))
    for index, line in enumerate(stage_lines):
        _put(panel, _truncate(line, 82), 30, 315 + index * 16, 0.35, THEME["muted"])

    _panel(panel, 14, 342, PANEL_W - 14, COMP_H - 14, "Live ROCm outputs", THEME["blue"])
    _put(panel, time.strftime("%H:%M:%S"), PANEL_W - 93, 362, 0.36, THEME["muted"])
    keys = ["depth", "normal", "segmentation", "gray", "blur", "sobel"]
    labels = ["K1 depth", "K2 normal", "K3 segmentation", "K5 grayscale", "K6 Gaussian blur", "K7 Sobel edges"]
    thumbs = dict(thumbnails or {})
    thumb_w = 180
    thumb_h = 135
    start_x = 28
    start_y = 372
    gap_x = 12
    gap_y = 34
    for index, (key, label) in enumerate(zip(keys, labels)):
        col = index % 3
        row = index // 3
        x = start_x + col * (thumb_w + gap_x)
        y = start_y + row * (thumb_h + gap_y)
        if key in thumbs:
            image = _image_thumbnail(key, thumbs[key], thumb_w, thumb_h)
        else:
            image = np.zeros((thumb_h, thumb_w, 3), dtype=np.uint8)
        panel[y : y + thumb_h, x : x + thumb_w] = image
        cv2.rectangle(panel, (x, y), (x + thumb_w, y + thumb_h), THEME["border"], 1)
        _put(panel, label, x, y + thumb_h + 18, 0.42, THEME["text"], 1)

    tactile = dict(tactile or {})
    tactile_y = 712
    tactile_size = 86
    _put(panel, "K4 left tactile", 28, tactile_y - 8, 0.40, THEME["text"])
    _put(panel, "K4 right tactile", 128, tactile_y - 8, 0.40, THEME["text"])
    left_image = _tactile_thumbnail(left_tactile, tactile_size, tactile_size)
    right_image = _tactile_thumbnail(right_tactile, tactile_size, tactile_size)
    panel[tactile_y : tactile_y + tactile_size, 28 : 28 + tactile_size] = left_image
    panel[tactile_y : tactile_y + tactile_size, 128 : 128 + tactile_size] = right_image
    cv2.rectangle(panel, (28, tactile_y), (28 + tactile_size, tactile_y + tactile_size), THEME["border"], 1)
    cv2.rectangle(panel, (128, tactile_y), (128 + tactile_size, tactile_y + tactile_size), THEME["border"], 1)
    tactile_color = THEME["green"] if tactile.get("secure") else THEME["orange"]
    _put(
        panel,
        f"secure  {bool(tactile.get('secure', False))}",
        242,
        730,
        0.48,
        tactile_color,
        2,
    )
    _put(
        panel,
        f"contact {int(tactile.get('n_contact', 0))}/{int(tactile.get('n_taxels', 128))}",
        242,
        754,
        0.42,
        THEME["text"],
    )
    _put(
        panel,
        f"peak {float(tactile.get('peak_mm', 0.0)):.3f} mm   "
        f"force {float(tactile.get('grip_force_N', 0.0)):.2f} N",
        242,
        779,
        0.42,
        THEME["text"],
    )
    return panel


def compose_hud_frame(
    scene_rgb: Any,
    *,
    status: str = "",
    title: str = "PHYSim · Physical AI",
    user_input: str = "",
    planner_raw: Any = None,
    plan: Any = None,
    stages: Sequence[Any] | None = None,
    thumbnails: Mapping[str, Any] | None = None,
    tactile: Mapping[str, Any] | None = None,
    left_tactile: Any = None,
    right_tactile: Any = None,
    gpu: Mapping[str, Any] | None = None,
) -> np.ndarray:
    """Compose a 1440×816 BGR frame from notebook-owned state."""

    left = _render_main(scene_rgb, status, tactile, title)
    right = _render_brain(
        gpu,
        user_input,
        planner_raw,
        plan,
        stages,
        thumbnails,
        tactile,
        left_tactile,
        right_tactile,
    )
    frame = np.concatenate([left, right], axis=1)
    if frame.shape != (COMP_H, COMP_W, 3):
        raise ValueError(f"unexpected HUD shape: {frame.shape}")
    return np.ascontiguousarray(frame, dtype=np.uint8)


class FFmpegHUDWriter:
    """Stream BGR frames to an H.264 MP4 using the system ffmpeg."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        fps: int = 25,
        width: int = COMP_W,
        height: int = COMP_H,
    ) -> None:
        self.path = str(path)
        self.fps = int(fps)
        self.width = int(width)
        self.height = int(height)
        self._process: subprocess.Popen[bytes] | None = None
        self.frames_written = 0

    def open(self) -> "FFmpegHUDWriter":
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise FileNotFoundError("ffmpeg is not available in PATH")
        command = [
            ffmpeg,
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{self.width}x{self.height}",
            "-r",
            str(self.fps),
            "-i",
            "-",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            self.path,
        ]
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return self

    def write(self, frame: Any) -> None:
        if self._process is None:
            self.open()
        arr = np.ascontiguousarray(frame, dtype=np.uint8)
        if arr.shape != (self.height, self.width, 3):
            raise ValueError(
                f"frame shape {arr.shape} does not match {(self.height, self.width, 3)}"
            )
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("ffmpeg writer is not available")
        try:
            self._process.stdin.write(arr.tobytes())
        except BrokenPipeError as error:
            stderr = (
                self._process.stderr.read().decode("utf-8", errors="replace")
                if self._process.stderr is not None
                else ""
            )
            raise RuntimeError(f"ffmpeg stopped while writing a frame: {stderr}") from error
        self.frames_written += 1

    def close(self) -> None:
        if self._process is None:
            return
        if self._process.stdin is not None:
            self._process.stdin.close()
        try:
            return_code = self._process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self._process.kill()
            return_code = self._process.wait(timeout=5)
        stderr = (
            self._process.stderr.read().decode("utf-8", errors="replace")
            if self._process.stderr is not None
            else ""
        )
        self._process = None
        if return_code != 0:
            raise RuntimeError(f"ffmpeg exited with status {return_code}: {stderr}")

    def __enter__(self) -> "FFmpegHUDWriter":
        return self.open()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def __del__(self) -> None:
        if self._process is not None:
            try:
                self.close()
            except Exception:
                pass


class GPUMonitor:
    """Background AMD GPU telemetry reader with a thread-safe snapshot API."""

    def __init__(self, interval: float = 0.5) -> None:
        self.interval = float(interval)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._card = self._find_card()
        self._state: dict[str, Any] = {
            "available": False,
            "source": "not available",
            "gpu_pct": 0.0,
            "vram_used_mb": 0.0,
            "vram_total_mb": 0.0,
            "vram_pct": 0.0,
            "temp_c": 0.0,
            "gpu_history": collections.deque(maxlen=60),
            "vram_history": collections.deque(maxlen=60),
        }

    @staticmethod
    def _find_card() -> Path | None:
        drm = Path("/sys/class/drm")
        if not drm.exists():
            return None
        for card in sorted(drm.glob("card[0-9]*")):
            vendor = card / "device" / "vendor"
            try:
                if vendor.read_text().strip().lower() == "0x1002":
                    return card
            except OSError:
                continue
        return None

    @staticmethod
    def _read_number(path: Path) -> float:
        try:
            return float(path.read_text().strip())
        except (OSError, ValueError):
            return 0.0

    def _sample(self) -> None:
        if self._card is None:
            return
        device = self._card / "device"
        gpu_pct = self._read_number(device / "gpu_busy_percent")
        used = self._read_number(device / "mem_info_vram_used") / (1024**2)
        total = self._read_number(device / "mem_info_vram_total") / (1024**2)
        vram_pct = used / total * 100.0 if total else 0.0
        temp_c = 0.0
        for temp in sorted(device.glob("hwmon/hwmon*/temp1_input")):
            temp_c = self._read_number(temp) / 1000.0
            if temp_c:
                break
        with self._lock:
            self._state.update(
                {
                    "available": True,
                    "source": str(device),
                    "gpu_pct": gpu_pct,
                    "vram_used_mb": used,
                    "vram_total_mb": total,
                    "vram_pct": vram_pct,
                    "temp_c": temp_c,
                }
            )
            self._state["gpu_history"].append(gpu_pct)
            self._state["vram_history"].append(vram_pct)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self.interval)

    def start(self) -> "GPUMonitor":
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = copy.deepcopy(self._state)
        state["gpu_history"] = list(state["gpu_history"])
        state["vram_history"] = list(state["vram_history"])
        return state

    def __enter__(self) -> "GPUMonitor":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()
