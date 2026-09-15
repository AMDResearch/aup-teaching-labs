"""Terminal-stdin keyboard teleoperator.

Drop-in replacement for `lerobot.teleoperators.keyboard.KeyboardTeleop` on systems
where pynput cannot capture key events (Wayland, headless, SSH). Instead of a global
key hook, it puts the controlling terminal into cbreak mode and reads keystrokes from
stdin, so the terminal window must keep focus while teleoperating.

stdin gives key *presses* only -- there are no release events. A key is therefore
reported as held for `hold_time_s` after the last time it was seen, and the terminal's
own auto-repeat (which fires while a key is physically held) keeps refreshing that
window. A single tap yields a short nudge; holding yields continuous motion.

Ctrl+C still raises KeyboardInterrupt because cbreak mode leaves ISIG enabled.
"""

import atexit
import os
import select
import sys
import termios
import time
import tty


class StdinKeyboardTeleop:
    def __init__(self, hold_time_s: float = 0.15):
        self.hold_time_s = hold_time_s
        self._fd = sys.stdin.fileno()
        self._saved_term = None
        self._last_seen: dict[str, float] = {}

    @property
    def is_connected(self) -> bool:
        return self._saved_term is not None

    def connect(self) -> None:
        if not sys.stdin.isatty():
            raise RuntimeError(
                "StdinKeyboardTeleop needs a terminal: stdin is not a TTY. "
                "Run the script directly in a terminal, not through a pipe."
            )
        self._saved_term = termios.tcgetattr(self._fd)
        # cbreak: no line buffering, no echo, but keep ISIG so Ctrl+C works.
        tty.setcbreak(self._fd)
        atexit.register(self._restore_terminal)
        print("[KEYBOARD] stdin keyboard active - keep this terminal focused. Ctrl+C to quit.")

    def calibrate(self) -> None:
        pass

    def configure(self) -> None:
        pass

    def _restore_terminal(self) -> None:
        if self._saved_term is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved_term)
            self._saved_term = None

    def _read_available(self) -> list[str]:
        chars: list[str] = []
        while select.select([sys.stdin], [], [], 0)[0]:
            data = os.read(self._fd, 1024)
            if not data:
                break
            chars.extend(data.decode("utf-8", "ignore"))
        return chars

    def get_action(self) -> dict[str, None]:
        if not self.is_connected:
            raise RuntimeError("StdinKeyboardTeleop is not connected. Run `.connect()` first.")

        now = time.monotonic()
        for char in self._read_available():
            self._last_seen[char] = now

        for char in [c for c, t in self._last_seen.items() if now - t > self.hold_time_s]:
            del self._last_seen[char]

        return dict.fromkeys(self._last_seen, None)

    def send_feedback(self, feedback) -> None:
        pass

    def disconnect(self) -> None:
        self._restore_terminal()
