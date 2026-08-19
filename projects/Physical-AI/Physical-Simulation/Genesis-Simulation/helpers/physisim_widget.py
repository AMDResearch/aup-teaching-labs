# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""ipywidgets controller for live PhySim HUD interaction."""

from __future__ import annotations

import html
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import ipywidgets as widgets
import numpy as np

from .physisim_hud import FFmpegHUDWriter


class LiveHUDController:
    """Display live HUD frames and dispatch notebook-owned simulation actions."""

    ACTIONS = (
        ("Reset", "reset"),
        ("Approach", "approach"),
        ("Close to Secure", "close"),
        ("Lift", "lift"),
        ("Lower", "lower"),
        ("Release", "release"),
        ("Shutdown HUD", "shutdown"),
    )

    def __init__(
        self,
        *,
        targets: tuple[str, ...] = ("red", "green", "blue"),
        contact_threshold: float = 5e-4,
        secure_taxels: int = 12,
        export_path: str = "Videos/video_05_hud.mp4",
        export_fps: int = 25,
        max_frames: int = 500,
    ) -> None:
        self.handlers: dict[str, Callable[["LiveHUDController"], Any]] = {}
        self.busy = False
        self.export_fps = int(export_fps)
        self.max_frames = int(max_frames)
        self._captured_jpegs: list[bytes] = []

        self.image = widgets.Image(
            format="jpeg",
            layout=widgets.Layout(
                width="auto",
                height="auto",
                max_width="100%",
                max_height="calc(100vh - 160px)",
                object_fit="contain",
                margin="0 auto",
                display="none",
            ),
        )
        self.status = widgets.HTML(
            value="<b>Status:</b> Initialize the live simulation.",
            layout=widgets.Layout(width="100%"),
        )
        self.target = widgets.Dropdown(
            options=targets,
            value=targets[0],
            description="Target",
        )
        self.scene_layout = widgets.Dropdown(
            options=(("Default", "default"), ("Random (seeded)", "random")),
            value="default",
            description="Layout",
            layout=widgets.Layout(width="240px"),
        )
        self.layout_seed = widgets.BoundedIntText(
            value=42,
            min=0,
            max=2_147_483_647,
            description="Seed",
            layout=widgets.Layout(width="210px"),
        )
        self.contact_threshold = widgets.FloatLogSlider(
            value=contact_threshold,
            base=10,
            min=-6,
            max=-2,
            step=0.1,
            description="Contact (m)",
            readout_format=".2e",
            continuous_update=False,
            layout=widgets.Layout(width="360px"),
        )
        self.secure_taxels = widgets.IntSlider(
            value=secure_taxels,
            min=1,
            max=128,
            step=1,
            description="Secure taxels",
            continuous_update=False,
            layout=widgets.Layout(width="360px"),
        )
        self.export_path = widgets.Text(
            value=export_path,
            description="Export",
            layout=widgets.Layout(width="520px"),
        )
        self.output = widgets.Output(layout=widgets.Layout(width="100%"))

        self.action_buttons: dict[str, widgets.Button] = {}
        for label, action in self.ACTIONS:
            button = widgets.Button(description=label, button_style="")
            button.on_click(lambda _button, name=action: self._dispatch(name))
            self.action_buttons[action] = button

        self.export_button = widgets.Button(
            description="Export MP4",
            button_style="success",
            icon="save",
        )
        self.export_button.on_click(self._on_export)
        self.clear_button = widgets.Button(
            description="Clear Frames",
            icon="trash",
        )
        self.clear_button.on_click(self._on_clear)

        if "approach" in self.action_buttons:
            controls = widgets.GridspecLayout(
                2,
                12,
                layout=widgets.Layout(width="100%", grid_gap="4px"),
            )
            for control in (
                self.target,
                self.scene_layout,
                self.layout_seed,
                self.contact_threshold,
                self.secure_taxels,
                self.export_path,
                self.export_button,
                self.clear_button,
                *self.action_buttons.values(),
            ):
                control.layout.width = "auto"
            controls[0, 0:2] = self.target
            controls[0, 2:4] = self.scene_layout
            controls[0, 4:6] = self.layout_seed
            controls[0, 6:9] = self.contact_threshold
            controls[0, 9:12] = self.secure_taxels
            controls[1, 0] = self.action_buttons["reset"]
            controls[1, 1] = self.action_buttons["approach"]
            controls[1, 2:4] = self.action_buttons["close"]
            controls[1, 4] = self.action_buttons["lift"]
            controls[1, 5] = self.action_buttons["lower"]
            controls[1, 6] = self.action_buttons["release"]
            controls[1, 7] = self.action_buttons["shutdown"]
            controls[1, 8:10] = self.export_path
            controls[1, 10] = self.export_button
            controls[1, 11] = self.clear_button
        else:
            # LiveAgentController replaces this temporary shell below.
            controls = widgets.VBox()
        self.widget = widgets.VBox([self.status, controls, self.image, self.output])

    @property
    def captured_frames(self) -> int:
        return len(self._captured_jpegs)

    def bind(self, action: str, handler: Callable[["LiveHUDController"], Any]) -> None:
        if action not in self.action_buttons:
            raise KeyError(f"unknown action: {action}")
        self.handlers[action] = handler

    def set_status(self, message: str, *, error: bool = False) -> None:
        # Status text can carry planner output, LLM replies and exception strings.
        color = "#d32f2f" if error else "#2e7d32"
        self.status.value = (
            f"<b>Status:</b> <span style='color:{color}'>{html.escape(message)}</span> "
            f"<span style='color:#777'>· captured {self.captured_frames} frames</span>"
        )

    def update(self, frame: Any, status: str, *, capture: bool = True) -> None:
        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), 88],
        )
        if not ok:
            raise RuntimeError("OpenCV failed to encode the HUD frame")
        jpeg = encoded.tobytes()
        self.image.value = jpeg
        self.image.layout.display = "block"
        if capture:
            if len(self._captured_jpegs) >= self.max_frames:
                self._captured_jpegs.pop(0)
            self._captured_jpegs.append(jpeg)
        self.set_status(status)

    def clear_frames(self) -> None:
        self._captured_jpegs.clear()
        self.set_status("Captured frame buffer cleared.")

    def export(self, path: str | None = None) -> str:
        if not self._captured_jpegs:
            raise RuntimeError("No live HUD frames have been captured")
        output = path or self.export_path.value
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        writer = FFmpegHUDWriter(output, fps=self.export_fps).open()
        try:
            for jpeg in self._captured_jpegs:
                frame = cv2.imdecode(
                    np.frombuffer(jpeg, dtype=np.uint8),
                    cv2.IMREAD_COLOR,
                )
                if frame is None:
                    raise RuntimeError("Failed to decode a captured HUD frame")
                writer.write(frame)
        finally:
            writer.close()
        self.set_status(
            f"Exported {writer.frames_written} frames to {output}."
        )
        return output

    def _set_buttons_disabled(self, disabled: bool) -> None:
        for button in self.action_buttons.values():
            button.disabled = disabled
        self.export_button.disabled = disabled
        self.clear_button.disabled = disabled

    def _dispatch(self, action: str) -> None:
        if self.busy:
            return
        handler = self.handlers.get(action)
        if handler is None:
            self.set_status(f"No handler is bound for {action}.", error=True)
            return
        self.busy = True
        self._set_buttons_disabled(True)
        self.set_status(f"Running {action}…")
        try:
            with self.output:
                handler(self)
        except Exception as error:
            self.set_status(f"{action} failed: {error}", error=True)
            with self.output:
                print(f"{type(error).__name__}: {error}")
        finally:
            self.busy = False
            self._set_buttons_disabled(False)

    def _on_export(self, _button: widgets.Button) -> None:
        if self.busy:
            return
        try:
            with self.output:
                path = self.export()
                print("Exported:", path)
        except Exception as error:
            self.set_status(f"Export failed: {error}", error=True)

    def _on_clear(self, _button: widgets.Button) -> None:
        self.clear_frames()


class LiveAgentController(LiveHUDController):
    """Live command console for the GS06 language-guided agent."""

    ACTIONS = (
        ("Run Command", "run"),
        ("Home", "home"),
        ("Stop", "stop"),
        ("Reset Scene", "reset"),
        ("Shutdown HUD", "shutdown"),
    )

    def __init__(
        self,
        *,
        contact_threshold: float = 5e-4,
        secure_taxels: int = 12,
        export_path: str = "Videos/video_06_hud.mp4",
        export_fps: int = 25,
        max_frames: int = 750,
    ) -> None:
        super().__init__(
            targets=("red", "green", "blue"),
            contact_threshold=contact_threshold,
            secure_taxels=secure_taxels,
            export_path=export_path,
            export_fps=export_fps,
            max_frames=max_frames,
        )
        self.command = widgets.Text(
            value="stack the blue cube on the red cube",
            placeholder="pick green | stack blue on red | home | stop",
            description="Command",
            layout=widgets.Layout(width="680px"),
        )
        self.planner_mode = widgets.Dropdown(
            options=(("Offline", "offline"), ("LLM endpoint", "llm")),
            value="offline",
            description="Planner",
            layout=widgets.Layout(width="220px"),
        )
        self.last_result: dict[str, Any] | None = None

        controls = widgets.GridspecLayout(
            3,
            12,
            layout=widgets.Layout(width="100%", grid_gap="4px"),
        )
        for control in (
            self.command,
            self.planner_mode,
            self.scene_layout,
            self.layout_seed,
            self.contact_threshold,
            self.secure_taxels,
            self.export_path,
            self.export_button,
            self.clear_button,
            *self.action_buttons.values(),
        ):
            control.layout.width = "auto"
        controls[0, 0:6] = self.command
        controls[0, 6:8] = self.planner_mode
        controls[0, 8:10] = self.scene_layout
        controls[0, 10:12] = self.layout_seed
        controls[1, 0:4] = self.contact_threshold
        controls[1, 4:8] = self.secure_taxels
        controls[1, 8:10] = self.action_buttons["run"]
        controls[1, 10:12] = self.action_buttons["reset"]
        controls[2, 0] = self.action_buttons["home"]
        controls[2, 1] = self.action_buttons["stop"]
        controls[2, 2:4] = self.action_buttons["shutdown"]
        controls[2, 4:9] = self.export_path
        controls[2, 9:11] = self.export_button
        controls[2, 11] = self.clear_button
        self.widget = widgets.VBox([self.status, controls, self.image, self.output])
