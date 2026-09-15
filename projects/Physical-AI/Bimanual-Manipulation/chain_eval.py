#!/usr/bin/env python
"""Run several trained policies back to back on the robot, in one continuous session.

    conda activate lerobot
    python chain_eval.py --stages act_open_blue,act_close_blue --checkpoint 005000
    python chain_eval.py --stages act_open_blue,smolvla_pick_all,act_close_blue \
                         --colour blue --checkpoint 005000,020000,005000
    python chain_eval.py --stages act_open_blue,act_close_blue --auto 25
    python chain_eval.py --list

    c    finish this stage, hand over to the next
    h    hold: stop sending actions, arm freezes where it is (press again to resume)
    x    abort, stop sending actions and exit
    ctrl-c  same as x

Why this exists rather than two `lerobot-rollout` calls in a row: rollout owns the robot, so a
second invocation disconnects and reconnects it. That drops torque -- the arm sags, and it sags
while holding a drawer open -- and re-opens both motor buses and both cameras, several seconds
during which the scene changes. The whole premise of the phase split is that each policy starts
exactly where the previous one ended, and a reconnect destroys precisely that. Here the robot is
connected once and only the policy behind the control loop is swapped, so the hand-over costs one
control tick.

Each stage gets its own policy, its own pre/post processor pair (the normalisation statistics
differ per dataset) and its own inference engine. Switching stages calls reset() on the incoming
engine so it does not carry over a stale action chunk from a previous run.

SAFETY
    * The head and wheels are left un-energised; the policies were trained with them still, and a
      policy that learned base-velocity sensor noise must not reach the wheels.
    * --max-step clamps how far any joint may be commanded from where it currently is, per tick.
      A checkpoint this early can emit a large jump, and at 15 fps an unclamped jump is a slam.
      The recorded data moves ~1 unit/tick and peaks near 9, so the default leaves plenty of room.
    * `h` freezes the arm without disconnecting, which is the fastest way to interrupt a rollout
      that is heading somewhere bad while keeping torque on.
"""

import argparse
import sys
import termios
import time
from pathlib import Path

import cv2
import numpy as np

from lerobot.cameras.opencv import OpenCVCameraConfig
from lerobot.configs import PreTrainedConfig
from lerobot.datasets import aggregate_pipeline_dataset_features, create_initial_features
from lerobot.policies import get_policy_class, make_pre_post_processors
from lerobot.processor import make_default_processors
from lerobot.robots.xlerobot import XLerobot, XLerobotConfig
from lerobot.rollout.inference.sync import SyncInferenceEngine
from lerobot.utils.constants import OBS_STR
from lerobot.utils.feature_utils import (
    build_dataset_frame,
    combine_feature_dicts,
    hw_to_dataset_features,
)
from lerobot.utils.robot_utils import precise_sleep

from stdin_keyboard import StdinKeyboardTeleop

OUT_BASE = Path.home() / "lerobot_out"
DATA = Path.home() / "lerobot_data"
V4L_BY_PATH = "/dev/v4l/by-path"
CAMERA_WINDOW = "xlerobot chain eval"

NEXT_KEY = "c"
HOLD_KEY = "h"
ABORT_KEY = "x"

HARDWARE_ERRORS = (OSError, RuntimeError, termios.error)

# The task string each phase was recorded under. ACT ignores it, but the pipeline still has to
# hand something to the policy, and getting it right costs nothing and matters the day a
# language-conditioned policy takes one of these slots.
PHASE_TASKS = {
    "open": "open the {colour} drawer",
    "pick": "put the red cube in the {colour} drawer",
    "close": "close the {colour} drawer",
}

# smolvla_base's config names its cameras camera1/2/3, and finetuning rewrites the action shape but
# leaves those names (and a stale observation.state shape of 6) untouched. So a SmolVLA checkpoint
# has to be fed under the names it thinks it wants, while ACT was trained on head/wrist directly
# and needs no map. camera3 is never provided; SmolVLA pads it, exactly as during training.
# Which arm each phase actually drives. The other one was held at a fixed pose throughout that
# phase's recording, so its channels carry no information the policy can use and its output is a
# memorised constant rather than a decision. Both facts are exploited below -- see Stage.mask.
DRIVEN_ARM = {"open": "right_arm", "pick": "left_arm", "close": "right_arm"}

SMOLVLA_RENAME = {
    "observation.images.head": "observation.images.camera1",
    "observation.images.wrist": "observation.images.camera2",
}


def parse_stage(name: str) -> tuple[str | None, str]:
    """'act_open_blue' -> ('open', 'blue');  'act_blue_arms' -> (None, 'blue').

    A stage need not be a phase model. Whole-task checkpoints are useful here as controls: running
    one that is known to work through this script is what separates a broken eval path from a
    broken policy, and there is no reason to make that awkward.
    """
    parts = name.split("_")
    for part in parts:
        if part in PHASE_TASKS:
            colour = parts[parts.index(part) + 1] if parts.index(part) + 1 < len(parts) else ""
            return part, colour
    for part in parts:
        if part in ("yellow", "blue", "green", "all"):
            return None, part
    sys.exit(f"cannot read a phase or colour out of {name!r}")


def list_checkpoints() -> None:
    print(f"{'model':26s} {'dims':6s} checkpoints")
    for d in sorted(OUT_BASE.glob("*/checkpoints")):
        cfg = d / "last" / "pretrained_model" / "config.json"
        dims = "?"
        if cfg.exists():
            import json

            shape = json.loads(cfg.read_text()).get("output_features", {}).get("action", {}).get("shape")
            dims = str(shape[0]) if shape else "?"
        cks = " ".join(sorted(p.name for p in d.iterdir() if p.name[0].isdigit()))
        print(f"{d.parent.name:26s} {dims:6s} {cks}")


class Stage:
    """One policy in the chain, with everything it needs to produce actions for this robot."""

    def __init__(self, name, checkpoint, robot, fps, device, dataset_features,
                 ordered_action_keys, n_action_steps=None, colour=None):
        self.name = name
        phase, parsed = parse_stage(name)
        # A model trained on all three colours has no colour in its name, so it cannot supply one:
        # `smolvla_pick_all` would otherwise be asked to "put the red cube in the all drawer", a
        # sentence that appears nowhere in its training data. For a language-conditioned policy the
        # task string is the only thing naming the target, so the caller has to say which drawer.
        self.colour = colour or parsed
        if self.colour == "all":
            sys.exit(f"{name} was trained on all three colours -- pass --colour yellow|blue|green "
                     f"to say which drawer to ask for")
        self.task = (PHASE_TASKS[phase] if phase else PHASE_TASKS["pick"]).format(colour=self.colour)
        path = OUT_BASE / name / "checkpoints" / checkpoint / "pretrained_model"
        if not path.exists():
            sys.exit(f"no checkpoint at {path}\n  see what exists:  python chain_eval.py --list")
        self.path = path

        cfg = PreTrainedConfig.from_pretrained(path)
        cfg.pretrained_path = path
        # ACT is trained with chunk_size == n_action_steps == 100: it computes a 100-action
        # sequence from one observation and executes all of it before looking again -- 6.7 s of
        # open-loop motion at 15 fps. That looks wrong, and re-planning every tick looks obviously
        # better, but on this robot it is the opposite. Measured with a trace: at one action per
        # observation the policy settled on a single pose within 10 ticks and held it for the
        # remaining 94, commanding a total of 0.2 units of travel where the demonstrations cover
        # 344. The pose it settled on was the mean of the demonstrated trajectory -- the output a
        # policy gives when its input is outside anything it was trained on.
        #
        # That is the trap: re-planning every tick means a single out-of-distribution observation
        # can be self-sustaining. The arm moves to the mean pose, the mean pose with this scene is
        # itself unlike any demonstration, so the next decision is the same one, forever. Executing
        # a whole chunk commits to the motion instead, which carries the arm back through states
        # the demonstrations do cover. Chunking is not a compromise here, it is what makes an
        # imitation policy recoverable.
        if n_action_steps:
            cfg.n_action_steps = n_action_steps
        policy = get_policy_class(cfg.type).from_pretrained(path, config=cfg)
        policy.to(device).eval()

        # The action width has to match what this robot advertises, or the mapping from policy
        # output to joint names is silently misaligned rather than an error.
        want = cfg.output_features["action"].shape[0]
        if want != len(ordered_action_keys):
            sys.exit(f"{name}: policy outputs {want} channels but the robot advertises "
                     f"{len(ordered_action_keys)} -- pass --arms-only to match a 12-channel "
                     f"policy, or drop it for a 17-channel one")

        rename = SMOLVLA_RENAME if cfg.type == "smolvla" else {}
        pre, post = make_pre_post_processors(
            policy_cfg=cfg,
            pretrained_path=path,
            dataset_stats=None,
            preprocessor_overrides={
                "device_processor": {"device": device},
                "rename_observations_processor": {"rename_map": rename},
            },
        )
        self.engine = SyncInferenceEngine(
            policy=policy,
            preprocessor=pre,
            postprocessor=post,
            dataset_features=dataset_features,
            ordered_action_keys=ordered_action_keys,
            task=self.task,
            device=device,
            robot_type=robot.name,
        )
        # Masking, and why a chained bimanual policy needs it.
        #
        # Each phase was recorded with one leader attached, so the idle arm sat at a fixed pose for
        # every frame. The policy therefore (a) reads that arm's channels as a constant and (b)
        # emits that same constant back. Neither is a decision about the idle arm -- it is the pose
        # that happened to be convenient during that recording session.
        #
        # Chaining breaks on both. `open` leaves the right arm out at the drawer; `pick` was
        # recorded with the right arm parked at rest, 48.7 units away. Left unmasked, the pick
        # policy reads a right-arm state 48.7 units outside anything it trained on -- and answers
        # the way any imitation policy answers an out-of-distribution input, by emitting the mean
        # of its training data, so the left arm barely moves. It also commands the right arm back
        # to rest, undoing the phase that just ran and asking a position servo for a 49-unit step,
        # which sags the rail hard enough to reset the USB adapter.
        #
        # So: feed the idle arm's channels the constant the policy expects, and throw its idle-arm
        # output away in favour of holding position. The driven arm is unaffected. This is what
        # trimming to a single arm would achieve, without the robot having to advertise 6 channels.
        self.driven = DRIVEN_ARM.get(phase)          # None for a whole-task model: drive everything
        self.idle_keys = ([k for k in ordered_action_keys
                           if k.startswith(("left_arm", "right_arm")) and not k.startswith(self.driven)]
                          if self.driven else [])
        self.idle_reference = {}
        if self.idle_keys:
            ref = demo_start_pose(phase, self.colour, self.idle_keys)
            if ref:
                self.idle_reference = ref
                print(f"[LOAD]   drives {self.driven}; the other arm is held and its channels are "
                      f"fed the pose it had in training")
            else:
                print("[LOAD]   ! no dataset to read the idle-arm reference from; running unmasked")
                self.idle_keys = []

        self.chunk = getattr(cfg, "n_action_steps", None)
        print(f"[LOAD] {name}  {cfg.type}  {want} channels  "
              f"chunk {getattr(cfg, 'chunk_size', '?')}, {self.chunk} action(s) per observation"
              + ("  [cameras renamed for smolvla]" if rename else "")
              + f"  task={self.task!r}")

    def reset(self):
        self.engine.reset()


def demo_start_pose(phase: str | None, colour: str, keys: list[str]) -> dict[str, float] | None:
    """The pose the demonstrations for this phase begin in, averaged over episodes."""
    import glob

    import pyarrow as pa
    import pyarrow.parquet as pq

    candidates = ([f"xlerobot_{phase}_{colour}_arms", f"xlerobot_{phase}_{colour}"] if phase
                  else [f"xlerobot_drawer_{colour}_arms", f"xlerobot_drawer_{colour}"])
    for name in candidates:
        root = DATA / name
        if (root / "meta" / "info.json").exists():
            break
    else:
        return None
    import json

    names = json.loads((root / "meta" / "info.json").read_text())["features"]["observation.state"]["names"]
    tabs = [pq.read_table(f, columns=["action", "episode_index"])
            for f in sorted(glob.glob(str(root / "data" / "**" / "*.parquet"), recursive=True))]
    if not tabs:
        return None
    t = pa.concat_tables(tabs)
    ep = np.array(t.column("episode_index").to_pylist())
    # The first *action* of each episode, not the first observed state: that is literally what
    # the incoming policy commands on its first tick, so it is the pose to be standing at.
    ac = np.array(t.column("action").to_pylist(), dtype=np.float32)
    starts = np.stack([ac[ep == e][0] for e in sorted(set(ep.tolist()))]).mean(0)
    pose = {n: float(v) for n, v in zip(names, starts)}
    return {k: pose[k] for k in keys if k in pose}


def ramp_to(robot, action_proc, target: dict[str, float], keys: list[str],
            fps: float, speed: float, label: str = "RAMP", min_s: float = 0.3,
            max_s: float = 8.0) -> None:
    """Drive the arm to `target` along a straight line before handing over to a policy.

    An imitation policy cannot bring itself to its own starting pose: every demonstration begins in
    the same small region, so nothing in the data says how to reach that region from elsewhere.
    Started outside it, the policy emits the mean of the trajectories it saw, the arm travels to
    that mean pose, and it stops -- the mean pose in the current scene resembles no demonstration,
    so the next decision repeats the last one. Measured on this robot: 0.2 units of commanded travel
    over 104 ticks, against 344 in the demonstrations.

    Every hand-over needs this, not just the first one. Phases recorded in a single session join up
    by construction -- open ends where close begins, 3.9 units apart at the worst joint. Phases
    recorded in different sessions do not: `pick` was recorded after swapping the leader to the left
    arm, with the right arm parked at rest, while `open` leaves the right arm out at the drawer. So
    the pick policy's first command asks the right shoulder to cross 48.7 units and the elbow 27.6,
    in one tick. A position servo answers a step that size at full torque on both joints at once,
    the rail sags, and the USB adapter resets -- which surfaces as EIO on the motor bus and looks
    for all the world like a broken cable. Measured on this rig, at both hand-overs into and out of
    pick.

    Duration follows the distance, so a join that is already continuous costs almost nothing while
    a 49-unit one is walked across in a couple of seconds.
    """
    start = {k: robot.get_observation()[k] for k in keys}
    # `target` covers only the joints the incoming policy drives. Every other key is commanded to
    # its present position for the whole ramp, so the idle arm holds exactly where it was left.
    moving = {k: abs(target[k] - start[k]) for k in target}
    if not moving:
        return
    worst = max(moving, key=moving.get)
    seconds = min(max(moving[worst] / speed, min_s), max_s)
    steps = max(1, int(seconds * fps))
    print(f"[{label}] {seconds:.1f}s to the demonstrated start pose "
          f"(furthest: {worst.replace('_arm', '').replace('.pos', '')} by {moving[worst]:.1f})")
    for i in range(1, steps + 1):
        a = i / steps
        action = {k: start[k] + a * (target[k] - start[k]) if k in target else start[k]
                  for k in keys}
        loop = time.perf_counter()
        try:
            obs = robot.get_observation()
            robot.send_action(action_proc((action, obs)))
        except HARDWARE_ERRORS as e:
            print(f"[{label}] {e}")
        precise_sleep(max(0.0, 1.0 / fps - (time.perf_counter() - loop)))
    obs = robot.get_observation()
    off = {k: abs(obs[k] - v) for k, v in target.items()}
    w = max(off, key=off.get)
    print(f"[{label}] arrived, worst residual {off[w]:.1f} on "
          f"{w.replace('_arm', '').replace('.pos', '')}")


def demo_ticks(phase: str | None, colour: str) -> int | None:
    """How many control ticks the longest demonstration of this phase takes.

    Ticks, not seconds. A chunked policy advances its trajectory by one action per call, whatever
    the wall clock is doing -- and the wall clock does very different things per policy here: ACT
    runs a forward in ~34ms and holds 15Hz, SmolVLA takes ~320ms and drops the loop to about 3Hz.
    A limit in seconds would cut the SmolVLA stage off around a quarter of the way through its
    motion while leaving the ACT stages untouched, which looks like the policy failing rather than
    the timer firing.
    """
    import json

    candidates = ([f"xlerobot_{phase}_{colour}_arms", f"xlerobot_{phase}_{colour}"] if phase
                  else [f"xlerobot_drawer_{colour}_arms", f"xlerobot_drawer_{colour}"])
    for name in candidates:
        info = DATA / name / "meta" / "info.json"
        if info.exists():
            d = json.loads(info.read_text())
            if not d["total_episodes"]:
                return None
            # The longest demonstration, not the mean: a stage cut off at the average length would
            # be stopped halfway through half its own training data. The margin on top covers the
            # policy being slower than the operator was.
            import glob

            import pyarrow as pa
            import pyarrow.parquet as pq
            tabs = [pq.read_table(f, columns=["episode_index"])
                    for f in sorted(glob.glob(str(DATA / name / "data" / "**" / "*.parquet"),
                                              recursive=True))]
            ep = np.array(pa.concat_tables(tabs).column("episode_index").to_pylist())
            return max(int((ep == e).sum()) for e in set(ep.tolist()))
    return None


def clear_port_locks(robot) -> None:
    """Drop the SDK's stale "port busy" flag after an interrupted transfer.

    scservo's protocol handler sets ``port.is_using`` around every transaction and clears it on the
    way out -- but not if the transfer raises, and ``txPacket`` calls ``clearPort()`` inside that
    window, which is exactly where a USB adapter that hiccups throws EIO. The flag then sticks, and
    every later attempt fails with "Port is in use!" instead of actually retrying, so the bus can
    never recover even once the device is back. Without this a single glitch ends the rollout.
    """
    for bus in (robot.bus1, robot.bus2):
        handler = getattr(bus, "port_handler", None)
        if handler is not None:
            handler.is_using = False


def make_cameras(args):
    fourcc = None if args.camera_fourcc.lower() in ("none", "auto") else args.camera_fourcc
    cams = {}
    for name, device in (("head", args.head_camera), ("wrist", args.wrist_camera)):
        if device.lower() == "none":
            continue
        cams[name] = OpenCVCameraConfig(
            index_or_path=device, fps=args.camera_fps,
            width=args.camera_width, height=args.camera_height, fourcc=fourcc)
    return cams


def show_cameras(frames, banner):
    tiles = []
    for name in sorted(frames):
        f = frames[name]
        if f is None or f.ndim != 3 or f.shape[-1] != 3:
            continue
        tile = cv2.cvtColor(f, cv2.COLOR_RGB2BGR)
        cv2.putText(tile, name, (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        tiles.append(tile)
    if not tiles:
        return
    h = min(t.shape[0] for t in tiles)
    canvas = np.hstack([t[:h] for t in tiles])
    cv2.putText(canvas, banner, (8, h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 220, 255), 2)
    cv2.imshow(CAMERA_WINDOW, canvas)
    cv2.waitKey(1)


def clamp_action(action: dict, obs: dict, max_step: float) -> tuple[dict, float]:
    """Limit how far each joint is commanded from where it actually is."""
    if max_step <= 0:
        return action, 0.0
    out, worst = {}, 0.0
    for k, v in action.items():
        cur = obs.get(k)
        if cur is None or not np.isfinite(v):
            out[k] = cur if cur is not None else 0.0
            continue
        delta = v - cur
        worst = max(worst, abs(delta))
        out[k] = cur + max(-max_step, min(max_step, delta))
    return out, worst


def disconnect_quietly(device, label):
    try:
        device.disconnect()
        print(f"[MAIN] {label} disconnected")
    except Exception as e:
        print(f"[MAIN] {label} disconnect failed: {e}")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list", action="store_true", help="show available checkpoints and exit")
    p.add_argument("--stages", default="act_open_blue,act_close_blue",
                   help="comma-separated output-dir names, run in this order")
    # One value applies to every stage; a comma-separated list gives one per stage. The chain
    # mixes policies whose checkpoints are numbered on different schedules -- ACT saves every 1000
    # and SmolVLA every 5000 -- so a single number cannot address all of them.
    p.add_argument("--checkpoint", default="last",
                   help="checkpoint step for every stage, 'last', or a comma-separated list with "
                        "one entry per stage (e.g. 005000,020000,005000)")
    p.add_argument("--fps", type=float, default=None,
                   help="control rate; defaults to the fps of the first stage's dataset")
    # Auto-advance is the normal way to run the chain: the phases were demonstrated as one
    # continuous motion, so pausing between them for a keypress puts the next policy at a start
    # state its predecessor never actually produces.
    p.add_argument("--auto", default="demo",
                   help="when to advance: 'demo' (default) times each stage from the longest "
                        "demonstration of that phase, a number for the same seconds on every "
                        "stage, a comma-separated list for one each, or 'off' to wait for C")
    p.add_argument("--auto-margin", type=float, default=1.4,
                   help="multiplier on the demonstrated duration, since a policy is usually "
                        "slower than the operator was")
    # 0 = leave the checkpoint alone (ACT: 100). Do not lower this without reading the note in
    # Stage.__init__ -- one action per observation deadlocked on the real robot.
    p.add_argument("--n-action-steps", type=int, default=0,
                   help="actions executed per policy call; 0 keeps what the checkpoint was trained "
                        "with (100 for ACT). Lower values re-plan more often but let the policy "
                        "settle into a fixed point if it starts out of distribution")
    # Off by default. Feetech position control makes torque proportional to (goal - present), so
    # clamping the goal to present+N also clamps the available force: the arm stalls the moment it
    # meets resistance -- against a drawer, or a gripper closing on a cube -- because the position
    # error it needs in order to pull is exactly what the clamp removes. lerobot-rollout has no
    # such limit, which is why this looked like a policy failure. Enable it only to tame a
    # checkpoint that is visibly throwing the arm around, and expect it to prevent real work.
    p.add_argument("--max-step", type=float, default=0.0,
                   help="per-tick joint change limit (0 = off, matching lerobot-rollout). "
                        "A non-zero value also caps the torque the arm can develop")
    p.add_argument("--arms-only", action="store_true", default=True,
                   help="advertise only the 12 arm channels (matches the trimmed datasets)")
    p.add_argument("--full-features", dest="arms_only", action="store_false",
                   help="advertise all 17 channels instead")
    p.add_argument("--head-wheels", action="store_true",
                   help="energise the head and wheels (off by default -- see SAFETY)")
    # Addressed by the adapter's serial number, not /dev/ttyACM*: cdc_acm hands out the lowest
    # free minor at enumeration, so an adapter that drops off the bus and returns can come back
    # under a different number -- this rig has been seen swapping the two arms between runs, which
    # drives every command onto the wrong motor with no error at all. Re-derive from
    # /dev/serial/by-id/ if an adapter is replaced (8 motors = left arm + head, 9 = right + wheels).
    p.add_argument("--port1", default="/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B14034094-if00")
    p.add_argument("--port2", default="/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B14115158-if00")
    p.add_argument("--robot-id", default=None)
    p.add_argument("--head-camera",
                   default=f"{V4L_BY_PATH}/pci-0000:bf:00.4-usb-0:1:1.0-video-index0")
    p.add_argument("--wrist-camera",
                   default=f"{V4L_BY_PATH}/pci-0000:bd:00.4-usb-0:1.3:1.0-video-index0")
    p.add_argument("--camera-fps", type=int, default=15)
    p.add_argument("--camera-width", type=int, default=640)
    p.add_argument("--camera-height", type=int, default=480)
    p.add_argument("--camera-fourcc", default="MJPG")
    p.add_argument("--colour", default=None, choices=("yellow", "blue", "green"),
                   help="which drawer the task strings should name. Required when a stage was "
                        "trained on all three colours (smolvla_pick_all); otherwise it overrides "
                        "the colour read from each model's name")
    # Units per second for every hand-over ramp, including the initial one. The demonstrations
    # move about 15 units/s, so 20 is brisk without being a step input. 0 disables all ramping,
    # which on this rig means a 49-unit jump into the pick stage and a USB adapter reset.
    p.add_argument("--ramp-speed", type=float, default=20.0,
                   help="units/second when walking the arm to the next stage's start pose "
                        "(0 = hand over instantly, and expect a current spike where two phases "
                        "were recorded in different sessions)")
    p.add_argument("--trace", default=None,
                   help="write a per-tick record of what was commanded and where the arm actually "
                        "went, as an .npz. Compare it against the demonstration to see whether the "
                        "policy is asking for the wrong thing or the arm is failing to follow")
    p.add_argument("--max-bus-errors", type=int, default=45,
                   help="consecutive failed reads tolerated before giving up (45 = 3s at 15fps). "
                        "A stale SDK port lock is cleared on every one of them, which is what lets "
                        "a USB hiccup recover instead of ending the run")
    p.add_argument("--no-window", action="store_true")
    p.add_argument("--device", default="cuda")
    return p.parse_args()


def main():
    args = parse_args()
    if args.list:
        list_checkpoints()
        return

    stage_names = [s.strip() for s in args.stages.split(",") if s.strip()]
    if not stage_names:
        sys.exit("--stages is empty")

    # Read the rate from the dataset the first stage was trained on. The policy learned the
    # displacement between adjacent frames, so running at another rate rescales the whole
    # trajectory: right poses, wrong speed, and grasp timing drifts.
    fps = args.fps
    if fps is None:
        import json

        phase, colour = parse_stage(stage_names[0])
        info = DATA / f"xlerobot_{phase}_{colour}_arms" / "meta" / "info.json"
        fps = json.loads(info.read_text())["fps"] if info.exists() else 15
        print(f"[MAIN] control rate {fps} fps (from {info.parent.parent.name})")

    # Serial ports are not exclusive on Linux: a second reader steals status packets from the
    # first, and the symptoms are indistinguishable from a hardware fault. Recording and
    # evaluating share the same two follower buses, so refuse rather than corrupt both.
    import subprocess
    if subprocess.run(["fuser", args.port1, args.port2],
                      capture_output=True).returncode == 0:
        busy = subprocess.run(["fuser", "-v", args.port1, args.port2],
                              capture_output=True, text=True)
        sys.exit(f"another process already has the follower buses open:\n{busy.stderr}")

    trace = [] if args.trace else None
    ordered_action_keys: list[str] = []
    robot = XLerobot(XLerobotConfig(
        port1=args.port1, port2=args.port2, id=args.robot_id,
        cameras=make_cameras(args),
        use_head_wheels=args.head_wheels,
        arms_only_features=args.arms_only,
    ))
    robot.connect()
    print(f"[MAIN] robot connected, {len(robot.action_features)} action channels")

    try:
        # Same feature plumbing build_rollout_context does, so the tensors line up with what was
        # recorded and trained on.
        teleop_proc, robot_action_proc, robot_obs_proc = make_default_processors()
        obs_features_hw = {k: v for k, v in robot.observation_features.items()
                           if isinstance(v, tuple) or v is float}
        action_features_hw = dict(robot.action_features)
        dataset_features = combine_feature_dicts(
            aggregate_pipeline_dataset_features(
                pipeline=teleop_proc,
                initial_features=create_initial_features(action=action_features_hw),
                use_videos=True),
            aggregate_pipeline_dataset_features(
                pipeline=robot_obs_proc,
                initial_features=create_initial_features(observation=obs_features_hw),
                use_videos=True),
        )
        hw_to_dataset_features(obs_features_hw, OBS_STR)   # validates the shapes
        ordered_action_keys = list(action_features_hw.keys())

        cks = [c.strip() for c in args.checkpoint.split(",") if c.strip()]
        if len(cks) == 1:
            cks *= len(stage_names)
        elif len(cks) != len(stage_names):
            sys.exit(f"--checkpoint has {len(cks)} entries but there are {len(stage_names)} "
                     f"stages; give one value for all of them or one per stage")
        stages = [Stage(n, c, robot, fps, args.device, dataset_features, ordered_action_keys,
                        args.n_action_steps, args.colour)
                  for n, c in zip(stage_names, cks)]

        # One duration per stage. 'demo' asks each phase's own recordings how long it takes, which
        # is the only source that does not require the operator to guess a number per colour.
        # Budgets are in ticks. A number given on the command line is still seconds, converted at
        # the nominal rate, because that is what a person means when they say "give it 30s".
        if args.auto.strip().lower() in ("off", "0", ""):
            budgets = [0] * len(stages)
        elif args.auto.strip().lower() == "demo":
            budgets = []
            for st in stages:
                phase, _ = parse_stage(st.name)
                n = demo_ticks(phase, st.colour)
                if n is None:
                    sys.exit(f"no demonstrations found to time {st.name}; pass --auto with seconds")
                budgets.append(int(n * args.auto_margin))
        else:
            vals = [float(v) for v in args.auto.split(",") if v.strip()]
            if len(vals) == 1:
                vals *= len(stages)
            elif len(vals) != len(stages):
                sys.exit(f"--auto has {len(vals)} values but there are {len(stages)} stages")
            budgets = [int(v * fps) for v in vals]

        keyboard = StdinKeyboardTeleop()
        keyboard.connect()

        print()
        print(f"  Chain: {' -> '.join(s.name for s in stages)}")
        print(f"  {NEXT_KEY.upper()} next stage   {HOLD_KEY.upper()} hold/resume   "
              f"{ABORT_KEY.upper()} abort   ctrl-c abort")
        if any(budgets):
            print("  auto-advance: " + ", ".join(
                f"{st.name} {b} ticks ({b / fps:.0f}s at {fps}Hz)"
                for st, b in zip(stages, budgets)))
        else:
            print(f"  no auto-advance -- press {NEXT_KEY.upper()} to move on")
        print(f"  max step {args.max_step} units/tick, head+wheels "
              f"{'ON' if args.head_wheels else 'off'}")
        input("  press ENTER to start...")

        index, held, prev, bus_errors = 0, False, set(), 0
        rate_ticks, rate_since = 0, time.perf_counter()
        period = 1.0 / fps
        stage_start = time.perf_counter()
        ticks = clamped = 0
        worst_delta = 0.0
        def hand_over(stage, label):
            """Walk the arm to where `stage` expects to start, before it takes control."""
            phase, _ = parse_stage(stage.name)
            # Only the arm this stage drives. The idle arm is held where the previous stage put it
            # -- moving it to a training-session rest pose is exactly the behaviour being avoided.
            keys = ([k for k in ordered_action_keys if k.startswith(stage.driven)]
                    if stage.driven else ordered_action_keys)
            target = demo_start_pose(phase, stage.colour, keys)
            if target is None:
                print(f"[{label}] no dataset for {phase}_{stage.colour}, handing over as is")
                return
            ramp_to(robot, robot_action_proc, target, ordered_action_keys, fps, args.ramp_speed,
                    label=label)

        if args.ramp_speed > 0:
            hand_over(stages[0], "HOME")

        def capture_hold(stage):
            """Freeze the idle arm wherever the previous stage finished."""
            if not stage.idle_keys:
                return {}
            o = robot.get_observation()
            return {k: o[k] for k in stage.idle_keys if k in o}

        stages[0].reset()
        idle_hold = capture_hold(stages[0])
        stage_start = time.perf_counter()
        print(f"\n[RUN] stage 1/{len(stages)}: {stages[0].name}")

        while True:
            loop_start = time.perf_counter()
            pressed = set(keyboard.get_action().keys())
            edge = pressed - prev

            if ABORT_KEY in edge:
                print("[RUN] aborted")
                break
            if HOLD_KEY in edge:
                held = not held
                print(f"[RUN] {'HELD -- not sending actions' if held else 'resumed'}")
            limit = budgets[index]
            advance = NEXT_KEY in edge or (limit > 0 and ticks >= limit)
            if advance:
                _el = max(time.perf_counter() - stage_start, 1e-6)
                print(f"[RUN] {stages[index].name} done: {ticks} ticks in {_el:.1f}s "
                      f"({ticks / _el:.1f} Hz, target {fps}), "
                      f"{clamped} clamped, worst requested step {worst_delta:.1f}")
                index += 1
                if index >= len(stages):
                    print("[RUN] chain complete")
                    break
                if args.ramp_speed > 0:
                    hand_over(stages[index], "RAMP")
                stages[index].reset()
                idle_hold = capture_hold(stages[index])
                stage_start = time.perf_counter()
                ticks = clamped = 0
                worst_delta = 0.0
                rate_ticks, rate_since = 0, time.perf_counter()
                print(f"[RUN] stage {index + 1}/{len(stages)}: {stages[index].name}")

            stage = stages[index]
            try:
                obs = robot.get_observation()
                bus_errors = 0
            except HARDWARE_ERRORS as e:
                bus_errors += 1
                # Only the first of a burst is worth printing; a bus that is down fails every tick
                # and hundreds of identical lines bury whatever came before them.
                if bus_errors == 1:
                    print(f"[RUN] observation failed: {e}")
                clear_port_locks(robot)
                if bus_errors >= args.max_bus_errors:
                    print(f"[RUN] bus unrecoverable after {bus_errors} ticks -- stopping. "
                          f"Check `ls -l /dev/ttyACM*`; the adapter may have re-enumerated.")
                    break
                prev = pressed
                precise_sleep(max(0.0, period - (time.perf_counter() - loop_start)))
                continue

            # Show the policy the idle arm where its training data always had it, not where this
            # chain happens to have left it.
            obs_in = obs
            if stage.idle_reference:
                obs_in = {**obs, **stage.idle_reference}
            obs_processed = robot_obs_proc(obs_in)
            frame = build_dataset_frame(dataset_features, obs_processed, prefix=OBS_STR)
            action_tensor = stage.engine.get_action(frame)

            if action_tensor is not None and not held:
                action = {k: action_tensor[i].item() for i, k in enumerate(ordered_action_keys)}
                # And keep the idle arm exactly where the previous stage left it, rather than
                # letting a memorised constant drag it back to that recording session's rest pose.
                action.update(idle_hold)
                action, worst = clamp_action(action, obs, args.max_step)
                worst_delta = max(worst_delta, worst)
                if worst > args.max_step > 0:
                    clamped += 1
                try:
                    robot.send_action(robot_action_proc((action, obs)))
                except HARDWARE_ERRORS as e:
                    print(f"[RUN] send_action failed: {e}")
                ticks += 1
                if trace is not None:
                    trace.append((time.perf_counter() - stage_start, index,
                                  [obs.get(k, float("nan")) for k in ordered_action_keys],
                                  [action[k] for k in ordered_action_keys]))

            if not args.no_window:
                elapsed = time.perf_counter() - stage_start
                banner = (f"{stage.name}  {ticks}"
                          + (f"/{budgets[index]}" if budgets[index] > 0 else "")
                          + f" ticks  {elapsed:4.1f}s"
                          + ("  [HELD]" if held else ""))
                show_cameras({k: v for k, v in obs.items() if k in robot.cameras}, banner)

            # Loop rate, reported once a second. A policy whose forward pass is slower than the
            # control period (SmolVLA is ~320ms against a 67ms budget at 15fps) does not merely run
            # its trajectory slowly -- it starves everything else sharing the loop. The cameras and
            # both motor buses hang off one USB hub on this rig, so a stalled consumer shows up
            # first as camera frame timeouts and then as the serial adapter dropping off the bus.
            # Whatever this number is, it is the first thing to look at when a stage misbehaves.
            rate_ticks += 1
            _now = time.perf_counter()
            if _now - rate_since >= 1.0:
                hz = rate_ticks / (_now - rate_since)
                if hz < fps * 0.8:
                    print(f"[RUN] loop at {hz:.1f} Hz, target {fps}"
                          + ("  -- policy inference is the bottleneck" if hz < fps * 0.4 else ""))
                rate_ticks, rate_since = 0, _now

            prev = pressed
            precise_sleep(max(0.0, period - (time.perf_counter() - loop_start)))

    except KeyboardInterrupt:
        print("\n[MAIN] interrupted")
    finally:
        if trace:
            import numpy as _np
            _np.savez(args.trace,
                      t=_np.array([r[0] for r in trace], dtype=_np.float32),
                      stage=_np.array([r[1] for r in trace], dtype=_np.int32),
                      observed=_np.array([r[2] for r in trace], dtype=_np.float32),
                      commanded=_np.array([r[3] for r in trace], dtype=_np.float32),
                      names=_np.array(ordered_action_keys),
                      stages=_np.array(stage_names))
            print(f"[MAIN] wrote {len(trace)} ticks to {args.trace}")
        # Cameras first: disconnect() closes the motor buses before the camera threads, and a
        # surviving camera thread turns a clean exit into "FATAL: exception not rethrown".
        if not args.no_window:
            cv2.destroyAllWindows()
        for name, cam in robot.cameras.items():
            if cam.is_connected:
                disconnect_quietly(cam, f"{name} camera")
        disconnect_quietly(robot, "Robot")


if __name__ == "__main__":
    main()
