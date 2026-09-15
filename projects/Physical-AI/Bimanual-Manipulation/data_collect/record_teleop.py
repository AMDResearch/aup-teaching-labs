"""Leader-arm teleoperation for XLeRobot.

Two SO-101 leader arms drive the two follower arms joint-to-joint (no inverse
kinematics involved), while the keyboard drives the head and the wheeled base.

    Head : W / S  = tilt up / down        A / D = pan left / right
    Base : I / K  = forward / backward    J / L = rotate left / right
           N / M  = speed up / down
    Rec  : R      = start / save episode  X     = discard and redo  (with --record)
    Quit : Ctrl+C

With `--record` every frame goes into a local LeRobotDataset -- both arms, the head, the base
velocities and every configured camera, all derived from the robot's own feature definitions.
Nothing is uploaded; the dataset stays under --dataset-root.

    python examples/9_xlerobot_teleop_leader.py \
        --left-leader /dev/ttyACM2 --right-leader /dev/ttyACM3 \
        --record --task "pick up the blue cube"

Both leader arms are optional. With neither connected only the keyboard controls are
active, which is the way to test head and base movement before wiring up the leaders:

    python examples/9_xlerobot_teleop_leader.py

    python examples/9_xlerobot_teleop_leader.py \
        --left-leader /dev/ttyACM2 --left-leader-id left_leader

    python examples/9_xlerobot_teleop_leader.py \
        --left-leader /dev/ttyACM2 --left-leader-id left_leader \
        --right-leader /dev/ttyACM3 --right-leader-id right_leader

Keyboard input is read from the terminal (see stdin_keyboard.py), so this works on
Wayland and over SSH -- but the terminal must keep focus.

The head and wrist cameras appear live, side by side, in an OpenCV window at the full loop rate
-- that is what you watch while collecting data. Rerun gets the joint scalars only; see
show_cameras() for why the images are kept out of it. Pass `none` to drop a camera,
`--no-window` to skip the video, `--no-viz` to skip rerun:

    python examples/9_xlerobot_teleop_leader.py --wrist-camera none

Note this needs the GUI build of OpenCV (`pip install opencv-python`), not the
opencv-python-headless that lerobot pins.
"""

import argparse
import os
import sys
import termios
import time
from pathlib import Path

import cv2
import numpy as np

from lerobot.cameras.opencv import OpenCVCameraConfig
from lerobot.datasets.lerobot_dataset import LeRobotDataset
# NOTE: these two live in lerobot.utils.feature_utils on this version. Older examples in this
# directory import them from lerobot.datasets.utils, which no longer re-exports them.
from lerobot.utils.constants import ACTION, DEFAULT_FEATURES, OBS_STR
from lerobot.utils.feature_utils import build_dataset_frame, hw_to_dataset_features
from lerobot.robots.xlerobot import XLerobot, XLerobotConfig
# Same retry count the robot itself uses, so a read here is no more fragile than the control loop.
from lerobot.robots.xlerobot.xlerobot import MOTOR_RETRIES
from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig
from lerobot.utils.robot_utils import precise_sleep
from lerobot.utils.visualization_utils import init_rerun, log_rerun_data

import sys
from pathlib import Path
# stdin_keyboard is shared with the other stages, so it lives in common/ rather than in
# this folder. Scripts are run by path, which puts only their own directory on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from stdin_keyboard import StdinKeyboardTeleop

# Head: motor 1 pans (+ = right), motor 2 tilts (+ = down).
HEAD_KEYMAP = {
    'w': ('head_motor_2.pos', -1),  # up
    's': ('head_motor_2.pos', +1),  # down
    'a': ('head_motor_1.pos', -1),  # left
    'd': ('head_motor_1.pos', +1),  # right
}

# Base. Values name entries in XLerobotConfig.teleop_keys, so the robot's own speed
# levels stay in charge of how fast each command actually drives the wheels.
BASE_KEYMAP = {
    'i': 'forward',
    'k': 'backward',
    'j': 'rotate_left',
    'l': 'rotate_right',
}
SPEED_KEYMAP = {
    'n': 'speed_up',
    'm': 'speed_down',
}

# Head positions are normalised to [-100, 100] because XLerobot runs use_degrees=False.
HEAD_LIMIT = 100.0

V4L_BY_PATH = '/dev/v4l/by-path'

CAMERA_WINDOW = 'xlerobot cameras'

# Recording controls. Kept clear of the head (WSAD), base (IKJL) and speed (NM) keys.
RECORD_KEY = 'r'
DISCARD_KEY = 'x'
# Advance to the next phase of a --phases recording. C for "continue".
NEXT_KEY = 'c'

# The three phases of the drawer task, and the task string each dataset is recorded under.
#
# Only `pick` is language conditioned in practice: it is the SmolVLA phase, and its string is the
# one that has to be reproduced character for character at eval time. The two ACT phases get a
# string too because a dataset needs one, but ACT never reads it -- which is also why open/close are
# trained per colour rather than as one language-conditioned model.
PHASE_TASKS = {
    'open': 'open the {colour} drawer',
    'pick': 'put the red cube in the {colour} drawer',
    'close': 'close the {colour} drawer',
}

# Everything hardware can throw at us mid-loop: motor bus errors and USB dropouts.
# ConnectionError (bad/missing status packet), TimeoutError (camera stopped delivering frames)
# and serial.SerialException are all OSError subclasses, so OSError covers them.
# termios.error is NOT -- it derives straight from Exception -- and it is exactly what
# tcdrain() raises when a USB serial adapter vanishes while its port is still open, which on
# this rig happens several times an hour. Leaving it out let a device dropout kill a whole
# recording session after the episodes had already been written.
HARDWARE_ERRORS = (OSError, RuntimeError, termios.error)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--port1', default='/dev/ttyACM0', help='left arm + head board')
    parser.add_argument('--port2', default='/dev/ttyACM1', help='right arm + wheels board')
    parser.add_argument('--robot-id', default=None, help='XLeRobot calibration id')
    parser.add_argument('--left-leader', default=None, help='left leader arm port, omit to disable')
    parser.add_argument('--left-leader-id', default='left_leader')
    parser.add_argument('--right-leader', default=None, help='right leader arm port, omit to disable')
    parser.add_argument('--right-leader-id', default='right_leader')
    # The leader's normalised output is fed straight to the follower as a goal position, so the
    # two calibrations have to describe the same physical range. Recalibrate a leader after
    # rebuilding it, swapping a motor, or if its range no longer matches its follower's.
    parser.add_argument('--calibrate-leaders', action='store_true',
                        help='prompt to recalibrate each leader arm before starting')
    parser.add_argument('--head-step', type=float, default=1.0, help='head units per cycle')
    parser.add_argument('--fps', type=float, default=30.0, help='control loop rate, also the dataset fps')
    # The head and wheels are dead weight on an arms-only task: they contribute five channels
    # that never move (or, for theta.vel, that carry only sensor noise a policy then learns to
    # reproduce). Leaving them un-energised keeps them out of the way without changing the
    # 17-wide observation, so datasets recorded either way stay compatible.
    parser.add_argument('--no-head-wheels', action='store_true',
                        help='leave the head and wheel motors un-energised and never command them')
    # /dev/videoN renumbers on every replug, and the two cameras report identical serials so
    # /dev/v4l/by-id/ collapses them into one symlink. by-path keys off the USB port, which only
    # changes if the camera is moved to a different socket -- run `ls /dev/v4l/by-path/` and pick
    # the `-video-index0` entries if you do move one (index1 is UVC metadata, not frames).
    parser.add_argument('--head-camera', default=f'{V4L_BY_PATH}/pci-0000:bf:00.4-usb-0:1:1.0-video-index0',
                        help="head camera device, 'none' to disable")
    parser.add_argument('--wrist-camera',
                        default=f'{V4L_BY_PATH}/pci-0000:bd:00.4-usb-0:1.3:1.0-video-index0',
                        help="wrist camera device, 'none' to disable")
    parser.add_argument('--camera-fps', type=int, default=30)
    parser.add_argument('--camera-width', type=int, default=640)
    parser.add_argument('--camera-height', type=int, default=480)
    # Both cameras hang off the same USB 2.0 hub. Uncompressed YUYV at 640x480x30 is ~147 Mbit/s
    # each, and two of those do not fit -- uvcvideo then quietly drops one stream's frame rate,
    # which shows up as one camera updating in bursts. MJPG is ~10x smaller and leaves room for
    # both. Pass 'none' to let the driver pick the format.
    parser.add_argument('--camera-fourcc', default='MJPG', help="camera pixel format, 'none' to auto-detect")
    parser.add_argument('--no-viz', action='store_true', help='skip the rerun stream')
    # Each viz frame costs four sync_reads across both motor buses on top of the writes
    # send_action already does, and pushes two images into rerun. Raise this if the viewer
    # struggles: it is the one knob that reduces both costs at once.
    parser.add_argument('--viz-every', type=int, default=2, help='log scalars to rerun every Nth cycle')
    parser.add_argument('--no-window', action='store_true', help='skip the OpenCV camera window')
    parser.add_argument('--max-comm-errors', type=int, default=5,
                        help='consecutive motor bus errors tolerated before giving up')
    parser.add_argument('--record', action='store_true', help='record episodes to a local dataset')
    parser.add_argument('--task', default=None,
                        help='language task description stored with every frame (required with --record)')
    parser.add_argument('--dataset-root', default=None,
                        help='dataset directory, defaults to ./datasets/<repo-id leaf>-<timestamp>')
    parser.add_argument('--repo-id', default='local/xlerobot_leader',
                        help='dataset repo_id, only a label while nothing is pushed')
    # torchcodec cannot load in this environment (it is built against upstream torch, not the
    # ROCm build here), so a video dataset records fine but cannot be read back. Storing frames
    # as images sidesteps the decoder entirely at the cost of a lot more disk.
    parser.add_argument('--images-not-video', action='store_true',
                        help='store frames as images instead of encoded video')
    parser.add_argument('--resume', action='store_true',
                        help='append episodes to the dataset at --dataset-root instead of creating one')
    # Phase mode: one continuous demonstration written into several datasets, with NEXT_KEY
    # marking the boundaries. Replaces --task/--repo-id/--dataset-root, which describe a single
    # dataset. See PhaseRecorder for why the split has to happen inside one demonstration.
    parser.add_argument('--phases', default=None,
                        help='comma-separated phases to record in sequence, e.g. open,pick,close '
                             f"(choices: {','.join(PHASE_TASKS)})")
    parser.add_argument('--colour', default=None,
                        help='which drawer, substituted into each phase task string (needs --phases)')
    parser.add_argument('--root-base', default=None,
                        help='parent directory for the per-phase datasets (needs --phases)')
    args = parser.parse_args()
    if args.phases:
        args.phases = [p.strip() for p in args.phases.split(',') if p.strip()]
        unknown = [p for p in args.phases if p not in PHASE_TASKS]
        if unknown:
            parser.error(f"unknown phase(s) {', '.join(unknown)}; choose from {', '.join(PHASE_TASKS)}")
        if not args.record:
            parser.error('--phases only applies with --record')
        if not args.colour:
            parser.error('--phases needs --colour, e.g. --colour blue')
        if not args.root_base:
            parser.error('--phases needs --root-base, the parent directory for the datasets')
        if args.task:
            parser.error('--phases sets the task per phase; drop --task')
    elif args.record and not args.task:
        parser.error('--record needs --task, e.g. --task "pick up the blue cube"')
    if args.colour and not args.phases:
        parser.error('--colour only applies with --phases')
    if args.resume and not args.record:
        parser.error('--resume only applies with --record')
    if args.resume and not (args.dataset_root or args.phases):
        parser.error('--resume needs --dataset-root pointing at the existing dataset')
    return args


def make_cameras(args):
    """Camera configs for XLerobotConfig, keyed by the name they get in the observation."""
    fourcc = None if args.camera_fourcc.lower() in ('none', 'auto') else args.camera_fourcc
    cameras = {}
    for name, device in (('head', args.head_camera), ('wrist', args.wrist_camera)):
        if not device or device.lower() == 'none':
            print(f"[MAIN] {name} camera: not configured")
            continue
        cameras[name] = OpenCVCameraConfig(
            index_or_path=Path(device),
            fps=args.camera_fps,
            width=args.camera_width,
            height=args.camera_height,
            fourcc=fourcc,
        )
        print(f"[MAIN] {name} camera: {device} @ {args.camera_width}x{args.camera_height} "
              f"{args.camera_fps}fps {fourcc or 'auto'}")
    return cameras


def make_leader(port, ident, side, recalibrate=False):
    if not port:
        print(f"[MAIN] {side} leader arm: not configured, that arm will not move")
        return None
    # use_degrees=False so the leader speaks the same [-100, 100] scale XLerobot expects.
    # The SOLeader default is True, which would send raw degrees and drive the arm wildly.
    leader = SO101Leader(SO101LeaderConfig(port=port, id=ident, use_degrees=False))
    leader.connect()
    print(f"[MAIN] {side} leader arm connected on {port} (id={ident})")
    if recalibrate:
        # connect() only calibrates when the stored values disagree with the file, so an already
        # calibrated leader is never offered the choice. Calling calibrate() directly does prompt
        # ("ENTER to keep the file, 'c' to recalibrate"), which is the only way to redo it.
        print(f"[MAIN] {side} leader: type 'c' at the prompt to recalibrate, ENTER to keep it")
        leader.calibrate()
    return leader


def clamp_head(value):
    return max(-HEAD_LIMIT, min(HEAD_LIMIT, value))


def show_cameras(frames):
    """Both feeds side by side in one OpenCV window, at the full loop rate.

    The images deliberately do NOT go to rerun. Its viewer back-pressures the SDK once its
    channel fills, which blocks this loop for seconds at a time -- that is what froze the video
    and let stale bytes pile up on the motor buses. cv2.imshow draws these two frames in well
    under a millisecond, so the display can never stall teleoperation.

    Requires the GUI build of OpenCV: lerobot pins opencv-python-headless, which raises
    "The function is not implemented" from imshow. Install opencv-python over it.
    """
    tiles = []
    for name in sorted(frames):
        frame = frames[name]
        # Skip anything that is not a colour frame (e.g. a RealSense depth plane).
        if frame is None or frame.ndim != 3 or frame.shape[-1] != 3:
            continue
        # OpenCVCamera hands back RGB, cv2.imshow expects BGR.
        tile = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        cv2.putText(tile, name, (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        tiles.append(tile)

    if not tiles:
        return

    # Crop to a common height rather than resize, so this stays a plain memcpy. The cameras are
    # configured identically, so in practice nothing is cropped at all.
    height = min(tile.shape[0] for tile in tiles)
    cv2.imshow(CAMERA_WINDOW, np.hstack([tile[:height] for tile in tiles]))
    cv2.waitKey(1)  # pumps the window event loop; without it nothing is drawn


class EpisodeRecorder:
    """Manual episode recording into a local LeRobotDataset.

    RECORD_KEY starts an episode and, pressed again, saves it. DISCARD_KEY throws the current one
    away so the demonstration can be redone. Frames whose joint state could not be read are
    skipped rather than aborting the episode; the count is reported when the episode is saved, so
    you can judge whether it is clean enough to keep.

    The dataset itself is created up front and spans every episode of the run. Nothing is pushed
    anywhere -- it stays under `root`.
    """

    def __init__(self, robot, root, repo_id, task, fps, use_video=True, resume=False,
                 task_label=None):
        self.task = task
        self.task_label = task_label or repo_id
        self.active = False
        self.frames = 0
        self.skipped = 0

        writer_kwargs = dict(image_writer_processes=4, image_writer_threads=4)
        if resume:
            # Appends to an existing dataset instead of starting a new one, so 20 episodes can be
            # collected over several sessions. Features and fps come from the dataset's own
            # metadata -- passing them again would be ignored, so mismatches are checked below.
            self.dataset = LeRobotDataset.resume(repo_id, root=root, **writer_kwargs)
            self.episodes = self.dataset.meta.total_episodes
            print(f"[REC] resuming {root}: {self.episodes} episode(s) already recorded")
            self._check_compatible(robot, fps, use_video)
        else:
            # Derived from the robot rather than hand-written, so both arms (12), the head (2),
            # the base velocities (3) and every camera are covered automatically.
            features = {
                **hw_to_dataset_features(robot.action_features, ACTION),
                **hw_to_dataset_features(robot.observation_features, OBS_STR, use_video=use_video),
            }
            self.dataset = LeRobotDataset.create(
                repo_id=repo_id,
                root=root,
                features=features,
                fps=int(fps),
                robot_type='xlerobot',
                use_videos=use_video,
                **writer_kwargs,
            )
            self.episodes = 0
            print(f"[REC] dataset at {root} (repo_id={repo_id}, "
                  f"{'video' if use_video else 'images'})")
        if task_label is None:
            print(f"[REC] task={task!r}")
            print(f"[REC] {RECORD_KEY.upper()} start/save episode    "
                  f"{DISCARD_KEY.upper()} discard and redo")

    def _check_compatible(self, robot, fps, use_video):
        """Refuse to append episodes that would not match what is already in the dataset.

        A silent mismatch here is expensive: it is only noticed at training time, by which point
        the session has been recorded and the dataset is a mix of incompatible episodes.
        """
        meta = self.dataset.meta
        problems = []
        if meta.fps != int(fps):
            problems.append(f"fps: dataset {meta.fps}, this run {int(fps)} (use --fps {meta.fps})")

        expected = {
            **hw_to_dataset_features(robot.action_features, ACTION),
            **hw_to_dataset_features(robot.observation_features, OBS_STR, use_video=use_video),
        }
        for key, spec in expected.items():
            have = meta.features.get(key)
            if have is None:
                problems.append(f"{key}: missing from the dataset")
            elif tuple(have['shape']) != tuple(spec['shape']):
                problems.append(f"{key}: dataset shape {tuple(have['shape'])}, "
                                f"this run {tuple(spec['shape'])}")
            elif have['dtype'] != spec['dtype']:
                problems.append(f"{key}: dataset dtype {have['dtype']}, this run {spec['dtype']}"
                                + (" (add --images-not-video)" if have['dtype'] == 'image' else ""))
        for key in meta.features:
            if key not in expected and key not in DEFAULT_FEATURES:
                problems.append(f"{key}: in the dataset but not produced by this run")

        if problems:
            raise SystemExit("[REC] cannot resume, this run does not match the dataset:\n  - "
                             + "\n  - ".join(problems))
        print("[REC] resume check passed: features and fps match")

    def toggle(self):
        if self.active:
            self.save()
        else:
            self.begin()

    def begin(self):
        """Start filling this dataset's episode buffer. Nothing is written until commit()."""
        self.active = True
        self.frames = self.skipped = 0
        print(f"[REC] recording episode {self.episodes}...")

    def pause(self):
        """Stop accepting frames, leaving the buffer intact for a later commit() or abort()."""
        self.active = False

    def commit(self):
        """Write the buffered episode, if it has anything in it."""
        self.active = False
        if not self.frames:
            self.dataset.clear_episode_buffer()
            return False
        self.dataset.save_episode()
        skipped = f", {self.skipped} frames skipped" if self.skipped else ""
        print(f"[REC]   {self.task_label}: episode {self.episodes}, {self.frames} frames{skipped}")
        self.episodes += 1
        return True

    def abort(self):
        """Throw the buffer away."""
        self.active = False
        if self.dataset.has_pending_frames():
            self.dataset.clear_episode_buffer()

    def add(self, obs, action, state_ok):
        """Append one frame. A frame with unreadable joint state is counted and dropped."""
        if not self.active:
            return
        if not state_ok:
            self.skipped += 1
            return
        frame = {
            **build_dataset_frame(self.dataset.features, obs, prefix=OBS_STR),
            **build_dataset_frame(self.dataset.features, action, prefix=ACTION),
            'task': self.task,
        }
        self.dataset.add_frame(frame)
        self.frames += 1

    def save(self):
        if not self.active:
            return
        self.active = False
        if not self.frames:
            print("[REC] nothing recorded, episode dropped")
            self.dataset.clear_episode_buffer()
            return
        self.dataset.save_episode()
        skipped = f", {self.skipped} frames skipped" if self.skipped else ""
        print(f"[REC] saved episode {self.episodes}: {self.frames} frames{skipped}")
        self.episodes += 1

    def discard(self):
        if not self.active:
            return
        self.active = False
        self.dataset.clear_episode_buffer()
        print(f"[REC] discarded episode {self.episodes} ({self.frames} frames)")

    def close(self):
        # An episode still open at Ctrl+C is kept -- losing a finished demonstration to a stray
        # keystroke is worse than keeping a slightly short one.
        if self.active:
            print("[REC] run ended mid-episode, saving it")
            self.save()
        elif self.dataset.has_pending_frames():
            self.dataset.clear_episode_buffer()
        self.dataset.finalize()
        print(f"[REC] {self.episodes} episode(s) written to {self.dataset.root}")


class PhaseRecorder:
    """One continuous demonstration, split across several datasets at the moment you say so.

    The task is three phases done in sequence by hand -- right arm opens the drawer, left arm
    moves the cube into it, right arm closes it -- but each phase wants its own policy, so each
    needs its own dataset. Splitting one physical demonstration at keypresses gives that, and
    gives it with the property that matters most when the policies are later chained: every phase
    begins exactly where the previous one ended, within one continuous motion. Recording the
    phases as separate demonstrations would train each policy on hand-off states that the
    preceding policy never actually produces.

    Each phase fills its own dataset's episode buffer and nothing is written until the cycle
    finishes, so DISCARD_KEY can throw the whole cycle away. That matters because a botched middle
    phase cannot be redone alone -- by then the drawer is open and the cube has moved.

        RECORD_KEY   start a cycle (also ends one early, keeping what is done)
        NEXT_KEY     finish this phase, start the next; after the last, commit the cycle
        DISCARD_KEY  throw away every phase of the current cycle
    """

    def __init__(self, robot, phases, colour, root_base, fps, use_video=True, resume=False):
        self.phases = list(phases)
        self.index = None                     # None = not in a cycle
        self.cycles = 0
        self.recorders = {}
        for phase in self.phases:
            name = f'xlerobot_{phase}_{colour}'
            root = Path(root_base) / name
            exists = (root / 'meta' / 'info.json').exists()
            if resume and not exists:
                print(f"[REC] {phase}: nothing at {root} yet, creating it")
            elif not resume and exists:
                sys.exit(f"[REC] {root} already exists -- pass --resume to append to it, "
                         f"or move it aside")
            self.recorders[phase] = EpisodeRecorder(
                robot, root, f'local/{name}',
                PHASE_TASKS[phase].format(colour=colour),
                fps, use_video=use_video, resume=resume and exists, task_label=phase)
        counts = ', '.join(f'{p}={r.episodes}' for p, r in self.recorders.items())
        print(f"[REC] phases {' -> '.join(self.phases)}   already recorded: {counts}")

    @property
    def active(self):
        return self.index is not None

    @property
    def current(self):
        return self.recorders[self.phases[self.index]] if self.active else None

    def _start(self, index):
        self.index = index
        phase = self.phases[index]
        print(f"[REC] cycle {self.cycles} | phase {index + 1}/{len(self.phases)}: {phase}")
        self.recorders[phase].begin()

    def _commit_cycle(self, note):
        written = [p for p in self.phases if self.recorders[p].commit()]
        self.index = None
        self.cycles += 1
        print(f"[REC] cycle {self.cycles - 1} {note}: wrote {len(written)} phase(s)"
              + (f" ({', '.join(written)})" if written else ""))

    def toggle(self):
        if not self.active:
            self._start(0)
        else:
            self.current.pause()
            self._commit_cycle(f"ended early at '{self.phases[self.index]}'")

    def next_phase(self):
        if not self.active:
            print(f"[REC] not in a cycle -- press {RECORD_KEY.upper()} to start one")
            return
        self.current.pause()
        if self.index + 1 < len(self.phases):
            self._start(self.index + 1)
        else:
            self._commit_cycle("complete")

    def discard(self):
        if not self.active:
            return
        reached = self.index + 1
        for phase in self.phases:
            self.recorders[phase].abort()
        self.index = None
        print(f"[REC] discarded the whole cycle ({reached} phase(s) in progress)")

    def add(self, obs, action, state_ok):
        if self.active:
            self.current.add(obs, action, state_ok)

    def close(self):
        if self.active:
            self.current.pause()
            self._commit_cycle("interrupted, keeping what was recorded")
        for r in self.recorders.values():
            r.close()


def check_within_range(robot):
    """Warn about joints sitting outside their calibrated range before torque goes on.

    Torque is off until connect(), so an arm can be pushed past a limit by hand -- and then the
    first command clamps the goal back into range and drives the joint all the way there. On a
    gravity-loaded joint like shoulder_lift that is a large forced move that trips the motor's own
    overload protection: it unloads, blinks, and teleoperation is dead before the first episode.
    Cheaper to point at the joint and let it be nudged back by hand.
    """
    problems = []
    for bus, motors in ((robot.bus1, robot.left_arm_motors + robot.head_motors),
                        (robot.bus2, robot.right_arm_motors)):
        for name in motors:
            try:
                pos = bus.read('Present_Position', name, normalize=False, num_retry=MOTOR_RETRIES)
                lo = bus.read('Min_Position_Limit', name, normalize=False, num_retry=MOTOR_RETRIES)
                hi = bus.read('Max_Position_Limit', name, normalize=False, num_retry=MOTOR_RETRIES)
            except HARDWARE_ERRORS as e:
                print(f"[MAIN] range check skipped for {name}: {e}")
                continue
            if not lo <= pos <= hi:
                out = pos - hi if pos > hi else lo - pos
                problems.append(f"{name}: at {pos}, {out} counts outside {lo}..{hi}")

    if problems:
        print("\n[MAIN] WARNING -- these joints are outside their calibrated range:")
        for p in problems:
            print(f"    {p}")
        print("[MAIN] Torque is already on, so they will be driven back into range as soon as the")
        print("[MAIN] first action is sent. On a loaded joint that can trip its overload")
        print("[MAIN] protection. Move them back by hand now, or expect the arm to lurch.")
    return problems


def clear_port_locks(robot):
    """Drop the SDK's stale "port busy" flag after an interrupted transfer.

    scservo's protocol handler sets ``port.is_using`` around every transaction and clears it on
    the way out -- but not if the transfer raises, and ``txPacket`` calls ``clearPort()`` inside
    that window, which is exactly where a vanished USB adapter throws. The flag then sticks, and
    every later attempt fails with "Port is in use!" instead of actually retrying, so the bus can
    never recover even once the device comes back.
    """
    for bus in (robot.bus1, robot.bus2):
        handler = getattr(bus, 'port_handler', None)
        if handler is not None:
            handler.is_using = False


def warn_throttled(counts, key, message, every=100):
    """Report a recurring fault without flooding the terminal.

    A device that drops off the bus fails on every single cycle, and hundreds of identical lines
    bury everything else. Print the first one, then one in every `every`, carrying the count.
    """
    counts[key] = counts.get(key, 0) + 1
    n = counts[key]
    if n == 1:
        print(f"[MAIN] {message}")
    elif n % every == 0:
        print(f"[MAIN] {message} (x{n})")


def disconnect_quietly(device, label):
    """Tear a device down without letting its own failure mask the error we are handling."""
    try:
        device.disconnect()
    except Exception as e:
        print(f"[MAIN] {label} disconnect failed: {e}")


def main():
    args = parse_args()

    # Rerun must be spawned BEFORE the cameras are opened. rr.spawn() forks a viewer process
    # that inherits whatever file descriptors are open at the time, so a viewer started after
    # robot.connect() holds on to the camera fds -- and when this script dies the viewer keeps
    # running and keeps the cameras busy, making every later run fail to open them.
    if not args.no_viz:
        # init_rerun defaults this to 8000 bytes, which flushes almost every image and wastes the
        # batcher. Batch into 1 MB chunks instead so the sender keeps up. init_rerun honours an
        # already-set value.
        os.environ.setdefault('RERUN_FLUSH_NUM_BYTES', str(1024 * 1024))
        init_rerun(session_name='xlerobot_teleop_leader')

    # max_relative_target is left at None on purpose: XLerobot's send_action looks up
    # present positions with the wrong keys, and ensure_safe_goal_position only accepts a
    # float while the config field is typed int -- either one raises. Align the leader arms
    # by hand before starting instead.
    robot = XLerobot(XLerobotConfig(
        port1=args.port1,
        port2=args.port2,
        id=args.robot_id,
        cameras=make_cameras(args),
        use_head_wheels=not args.no_head_wheels,
    ))
    try:
        robot.connect()
    except Exception:
        # A partial connect leaves camera read threads alive, which aborts the interpreter at
        # exit ("FATAL: exception not rethrown") and leaks the devices. Close what did open.
        for name, cam in robot.cameras.items():
            if cam.is_connected:
                disconnect_quietly(cam, f"{name} camera")
        raise
    print("[MAIN] Robot connected")
    check_within_range(robot)

    left_leader = right_leader = None
    try:
        left_leader = make_leader(args.left_leader, args.left_leader_id, 'Left',
                                  recalibrate=args.calibrate_leaders)
        right_leader = make_leader(args.right_leader, args.right_leader_id, 'Right',
                                   recalibrate=args.calibrate_leaders)
    except Exception:
        if left_leader:
            disconnect_quietly(left_leader, 'Left leader')
        disconnect_quietly(robot, 'Robot')
        raise

    if left_leader or right_leader:
        print("\n[MAIN] Move each leader arm to roughly match its follower arm before you start.")
        input("[MAIN] Press ENTER when the poses are aligned...")

    recorder = None
    if args.record and args.phases:
        recorder = PhaseRecorder(robot, args.phases, args.colour, args.root_base, args.fps,
                                 use_video=not args.images_not_video, resume=args.resume)
    elif args.record:
        root = args.dataset_root or (
            f"datasets/{args.repo_id.split('/')[-1]}-{time.strftime('%Y%m%d-%H%M%S')}")
        recorder = EpisodeRecorder(robot, root, args.repo_id, args.task, args.fps,
                                   use_video=not args.images_not_video, resume=args.resume)

    keyboard = StdinKeyboardTeleop()
    keyboard.connect()

    obs = robot.get_observation()
    head_target = {joint: obs[joint] for joint in ('head_motor_1.pos', 'head_motor_2.pos')}

    # An arm with no leader still needs an action every frame: build_dataset_frame reads every
    # name in the feature list, so a missing joint is a KeyError on the first recorded frame, not
    # a quietly-zeroed channel. Hold it at the pose it starts in -- that is both a target the arm
    # can actually track (torque is already on, so writing it stops the arm drifting) and an honest
    # label, since "stay where you are" is exactly what is being asked of it. Recording it as a
    # constant also means the channel has zero variance and trim_dims.py drops it cleanly.
    idle_action = {}
    for side, leader in (('left_arm', left_leader), ('right_arm', right_leader)):
        if leader is None:
            idle_action.update({k: v for k, v in obs.items()
                                if k.startswith(f'{side}_') and k in robot.action_features})
    if idle_action:
        print(f"[MAIN] no leader for {len(idle_action)} joint(s) -- holding them at their current "
              f"pose and recording that as the action")

    print()
    if args.no_head_wheels:
        print("  Head/base: disabled (--no-head-wheels), arms only")
    else:
        print("  Head : W/S tilt up/down    A/D pan left/right")
        print("  Base : I/K fwd/back        J/L rotate left/right    N/M speed")
    if isinstance(recorder, PhaseRecorder):
        print(f"  Rec  : {RECORD_KEY.upper()} start a cycle (again to end it early)")
        print(f"         {NEXT_KEY.upper()} finish this phase, start the next"
              f"        {DISCARD_KEY.upper()} discard the whole cycle")
        print(f"         phases: {' -> '.join(args.phases)}   "
              f"nothing is written until the cycle ends")
    elif recorder is not None:
        print(f"  Rec  : {RECORD_KEY.upper()} start/save episode   "
              f"{DISCARD_KEY.upper()} discard and redo")
    print("  Quit : Ctrl+C\n")

    prev_pressed = set()
    period = 1.0 / args.fps
    comm_errors = 0
    cycle = 0
    fault_counts = {}

    try:
        while True:
            loop_start = time.perf_counter()
            cycle += 1
            pressed = set(keyboard.get_action().keys())

            action = {}

            # Leader arms drive the followers joint-to-joint.
            if left_leader:
                action.update({f"left_arm_{k}": v for k, v in left_leader.get_action().items()})
            if right_leader:
                action.update({f"right_arm_{k}": v for k, v in right_leader.get_action().items()})
            action.update(idle_action)

            # Head: integrate key presses into a target position.
            for key, (joint, sign) in HEAD_KEYMAP.items():
                if key in pressed:
                    head_target[joint] = clamp_head(head_target[joint] + sign * args.head_step)
            action.update(head_target)

            # Base: translate our keys into the robot's own teleop_keys. Speed changes are
            # edge-triggered so holding N/M steps one level instead of slamming to the end.
            base_keys = [robot.config.teleop_keys[name]
                         for key, name in BASE_KEYMAP.items() if key in pressed]
            base_keys += [robot.config.teleop_keys[name]
                          for key, name in SPEED_KEYMAP.items()
                          if key in pressed and key not in prev_pressed]
            action.update(robot._from_keyboard_to_base_action(base_keys) or {})

            # Recording keys are edge-triggered: the keyboard reports a key as held for 0.15s
            # after the last press, so without this a single tap would toggle many times.
            if recorder is not None:
                if RECORD_KEY in pressed and RECORD_KEY not in prev_pressed:
                    recorder.toggle()
                if DISCARD_KEY in pressed and DISCARD_KEY not in prev_pressed:
                    recorder.discard()
                if (isinstance(recorder, PhaseRecorder)
                        and NEXT_KEY in pressed and NEXT_KEY not in prev_pressed):
                    recorder.next_phase()

            # Motor traffic. lerobot's sync_read/sync_write default to num_retry=0, so a single
            # dropped or corrupted packet raises and would end the session. Ride out isolated
            # glitches and only give up once the bus stays unresponsive for several cycles,
            # which means something is actually wrong rather than just noisy.
            try:
                robot.send_action(action)
                comm_errors = 0
            except HARDWARE_ERRORS as e:
                comm_errors += 1
                print(f"[MAIN] bus error {comm_errors}/{args.max_comm_errors}: {e}")
                # Without this the next attempt reports "Port is in use!" rather than retrying.
                clear_port_locks(robot)
                if comm_errors >= args.max_comm_errors:
                    raise SystemExit(
                        f"[MAIN] motor bus unrecoverable after {comm_errors} attempts. "
                        f"Episodes recorded so far are saved; check "
                        f"`ls -l /dev/serial/by-path/` -- the adapter may have re-enumerated "
                        f"under a different /dev/ttyACM number."
                    ) from e

            # One read per cycle, shared by the recorder, the window and rerun, so the cameras and
            # both motor buses are never hit twice. The joint state is only fetched when something
            # actually needs it: while recording it is mandatory, otherwise only on viz cycles.
            logging_viz = not args.no_viz and cycle % args.viz_every == 0
            need_state = (recorder is not None and recorder.active) or logging_viz

            obs, state_ok = None, False
            try:
                if need_state:
                    obs = robot.get_observation()
                    state_ok = True
                elif not args.no_window:
                    obs = robot.get_camera_observation()
            except HARDWARE_ERRORS as e:
                warn_throttled(fault_counts, 'state', f"state read failed: {e}")
                clear_port_locks(robot)
                # get_observation() reads both buses *before* it captures the cameras, so it never
                # reached the frames. Fetch those on their own to keep a live picture on screen.
                try:
                    obs = robot.get_camera_observation()
                except HARDWARE_ERRORS as e2:
                    warn_throttled(fault_counts, 'camera', f"camera read failed: {e2}")

            if recorder is not None:
                recorder.add(obs, action, state_ok)
                if not state_ok and recorder.active:
                    fault_counts['dropped frames'] = fault_counts.get('dropped frames', 0) + 1

            if obs is not None and not args.no_window:
                show_cameras({k: v for k, v in obs.items() if k in robot.cameras})

            # Images are excluded from rerun on purpose -- see show_cameras().
            if logging_viz and state_ok:
                log_rerun_data({k: v for k, v in obs.items() if k not in robot.cameras}, action)

            prev_pressed = pressed
            precise_sleep(max(0.0, period - (time.perf_counter() - loop_start)))
    except KeyboardInterrupt:
        print("\n[MAIN] Stopping.")
    finally:
        try:
            robot.stop_base()
        except Exception as e:
            print(f"[MAIN] stop_base failed: {e}")
        # Close the dataset before the hardware: save_episode() still needs the frames in hand,
        # and finalize() has video encoding to flush.
        if recorder is not None:
            try:
                recorder.close()
            except Exception as e:
                print(f"[MAIN] dataset close failed: {e}")
        # Fault tally, so a run can be compared against another with different cabling.
        print(f"[MAIN] cycles: {cycle}"
              + ''.join(f", {name} faults: {n}" for name, n in sorted(fault_counts.items())))
        cv2.destroyAllWindows()
        keyboard.disconnect()
        # Every teardown below is best-effort. A USB device that vanished mid-run leaves its file
        # descriptor open but dead, so closing it raises termios.error / OSError -- and an
        # exception escaping here aborts the interpreter ("FATAL: exception not rethrown") with a
        # core dump, right after the dataset was safely written. Report and carry on instead.
        for device, label in ((left_leader, 'Left leader'), (right_leader, 'Right leader'),
                              (robot, 'Robot')):
            if device is not None:
                disconnect_quietly(device, label)
        # XLerobot.disconnect() closes the motor buses before the cameras, so a dead bus stops it
        # part-way and the camera read threads stay alive -- which aborts the interpreter at exit.
        # Close them here too; disconnecting an already-closed camera is a no-op.
        for name, cam in robot.cameras.items():
            if cam.is_connected:
                disconnect_quietly(cam, f"{name} camera")
        print("[MAIN] Teleoperation ended.")


if __name__ == '__main__':
    main()
