<!-- Copyright (C) 2025 Advanced Micro Devices, Inc. All rights reserved. -->

[中文](SETUP.zh.md) | **English**

# Environment and hardware — XLeRobot + SmolVLA (AMD GPU)

The general single-arm steps are in `~/so101_pick/SETUP.md`. This document covers only
**this dual-arm machine's environment**: hardware addressing, the patches that have to be
applied to LeRobot itself, the software package traps, and the physical problems identified
by measurement.

**Workflow and design rationale (why three phases, why the idle arm is masked, the three silent
failure modes) are in [README.md](README.md) and are not repeated here.**

---

# Hardware

| device | contents | addressing |
|---|---|---|
| `port1` | left arm 6 + head 2 (id 1-8) | by-id `usb-1a86_USB_Single_Serial_5B14034094-if00` |
| `port2` | right arm 6 + wheels 3 (id 1-9) | by-id `usb-1a86_USB_Single_Serial_5B14115158-if00` |
| left / right leader | SO-101 leader | only connected while recording, `/dev/ttyACM*` |
| head camera | USB Camera 640x480 | by-path `pci-0000:bf:00.4-usb-0:1:...` (separate controller) |
| wrist camera | USB Camera 640x480 | by-path `pci-0000:bd:00.4-usb-0:1.3:...` |

17 Feetech STS3215 servos in total, on two buses.

## The follower bus must use by-id, never a `ttyACM` number

cdc_acm takes the **lowest free minor** at enumeration time, so `/dev/ttyACM0` is not a property
of the device. An adapter that drops off and reconnects picks up whatever number happens to be
free — this machine was measured swapping the two arms within three minutes: `3-1.2` was
`ttyACM0` at 11:27 and `ttyACM1` at 11:30.

Passing `--port1=/dev/ttyACM0` after that means commanding **the other arm**, and there is
**no error at all**: lerobot's handshake only checks "are the ids I want present", and a bus
with 9 servos passes a "needs 8" check perfectly. Every joint command lands on the wrong motor.

`/dev/serial/by-id/` maps to the adapter's own serial number and follows it across ports. All
scripts use it now. After replacing an adapter, re-derive the path from that directory (identify
by motor count: 8 = left arm + head, 9 = right arm + wheels).

## Cameras must use by-path, never by-id

Both cameras are the same model and their `ID_SERIAL` strings are identical, so
`/dev/v4l/by-id/` only creates **one** symlink and both point at it. The single-arm setup can
use by-id because its two cameras are different models.

`/dev/videoN` is worse: it renumbers on every replug (six or more times in one session on this
machine). Renumbering between recording and inference **silently swaps the two viewpoints**, and
the data is ruined.

```bash
ls -l /dev/v4l/by-path/*-usb-*-video-index0 | grep -v usbv2
```

`-video-index0` is the capture node; `-video-index1` is UVC metadata (it opens but has no
picture). The laptop's built-in AMD ISP shows up as `platform-` and is not your camera either.

Paths change when you move ports — `HEAD_CAM` / `WRIST_CAM` in the scripts have to be updated
to match.

---

# The four patches applied to LeRobot itself

`./setup_lerobot.sh` applies these; this section is what it does and why, for when upstream moves
and a patch stops applying. XLeRobot is not upstream, and adding `src/lerobot/robots/xlerobot/`
is not enough — the official CLI reads a hardcoded list. Every site is in a **tracked file**, so
`git pull` will conflict. Baseline commit `95211b98`.

**1. `src/lerobot/robots/utils.py`** — add a branch to `make_robot_from_config`:

```python
elif config.type == "xlerobot":
    from .xlerobot import XLerobot

    return XLerobot(config)
```

**2. `src/lerobot/scripts/lerobot_rollout.py`** — add it to the import list so
`@RobotConfig.register_subclass` fires; otherwise `--robot.type=xlerobot` is an invalid choice:

```python
from lerobot.robots import (  # noqa: F401
    ...
    xlerobot,
)
```

**3. `src/lerobot/rollout/context.py`** — remove the `.pos` filter:

```python
observation_features_hw = {
    k: v for k, v in all_obs_features.items() if isinstance(v, tuple) or v is float
}
action_features_hw = dict(robot.action_features)
```

Keeping only `.pos` gives 14 dims, but `lerobot-record` builds the dataset from all 17 dims of
`robot.observation_features`. The dimensions do not match and the normalizer raises
`The size of tensor a (14) must match the size of tensor b (17)`. The `TODO(Steven)` comment
upstream is about exactly this trade-off.

## The `use_head_wheels` switch

`XLerobotConfig.use_head_wheels` (default `True`). When set to `False`:

- `configure()` only enables torque on the 12 arm motors; head and wheels stay limp
- `send_action()` does not write to head or wheels
- `stop_base()` is skipped entirely

But they are **still read as normal**, so `observation_features` stays 17-dim and datasets
recorded in either mode remain mutually compatible. The head droops under gravity — that is
expected.

Scripts control it with the `HEAD_WHEELS` environment variable; on the CLI directly it is
`--robot.use_head_wheels=false` (draccus turns config fields into arguments automatically).

## Three layers of fault tolerance in `robots/xlerobot/xlerobot.py`

This is your own file and will not conflict with upstream:

- `MOTOR_RETRIES` — retry count for each bus read/write. Motors drop packets occasionally after
  power-up; lerobot only tries once by default, and a single dropped packet aborts an entire
  `lerobot-rollout` run. The fault is momentary, so a retry succeeds.
- The last-frame fallback in `get_camera_observation()` — when a camera stalls, reuse the last
  good frame and count it rather than raising. A camera that never succeeded still raises; it
  will not silently hand out empty frames.
- `CAMERA_READ_TIMEOUT_MS` — the 200 ms default is longer than one control tick, so a single
  late camera drags the whole loop down to 5 Hz. Set it close to the length of one tick.

All three **mask symptoms**. The root causes are in the last section.

---

# Software environment

## The container

`run.sh` starts it with the hardware attached. Two directories are mounted, because they are two
repositories:

| host | container |
|---|---|
| this lab directory | `/workspace/lab` (the working directory) |
| `~/lerobot` (override with `LEROBOT=`) | `/workspace/lerobot` |
| `~/lerobot_data`, `~/lerobot_out` | the same names under `/home/jovyan/` |

The image holds no lab code and no lerobot source, only dependencies.
`PYTHONPATH=/workspace/lerobot/src` puts the mounted source ahead of the copy installed in the
image, so the patched code is what runs and edits on the host take effect without a rebuild.

The rest of the flag list exists because each item fails differently when it is missing:

| flag | what happens without it |
|---|---|
| `--group-add render` | `/dev/kfd` and `/dev/dri/renderD*` belong to the render group; without it `torch.cuda.is_available()` is False and nothing says why |
| `--group-add dialout` | `/dev/ttyACM*` belong to dialout and the serial ports cannot be opened |
| `-v /dev/serial`, `-v /dev/v4l` | `--device` passes the character device but not the by-id / by-path symlinks, and every script addresses hardware by those stable names rather than by `ttyACM0` |
| `-e DISPLAY`, `-v /tmp/.X11-unix` | `cv2.imshow` has no X server (under Wayland this is XWayland, which is why `DISPLAY` is `:0`) |

Devices are attached only when present, so the container still starts with the robot unplugged;
training needs none of them.

Two consequences worth knowing. A container's supplementary groups are set by the daemon, which
is root, so **the host user does not have to be in `dialout`**: the serial ports are read-write
inside even where they are not outside (`sudo usermod -aG dialout $USER` fixes the host too).
And the host uid is 1000 and the container's `jovyan` is also 1000, so **the mounts need no
chown** — datasets read and checkpoints written back line up.

## Running without the container

The container (`Dockerfile` + `run.sh`, see `README.md`) is the supported path and
needs nothing on the host but Docker. What follows builds an equivalent conda
environment directly on the host instead — useful when Docker is unavailable, and
the source of the ROCm details the container inherits.

```bash
./build_env.sh                    # creates "lerobot" — the name every script header activates
./build_env.sh --name foo         # another name, leaving an existing one alone
./build_env.sh --force            # tear down an env of that name and redo it
./build_env.sh --resume-from 2    # continue a failed run at step N (1-5)
./build_env.sh --dry-run          # print the commands, change nothing
```

`environment.yml` is a `conda env export` snapshot and **cannot** be fed to
`conda env create -f`: the ROCm wheels are not on PyPI, `lerobot` is really an editable
install, opencv gets clobbered, and there are a few more traps only visible from inside the
snapshot. `build_env.sh` handles all of them, with the reasoning for each in its comments. It
refuses to touch an env that already exists, so running it by accident on a working machine is
safe.

## opencv must be swapped for the GUI build

lerobot depends on `opencv-python-headless`, which is built without GUI support, so `cv2.imshow`
fails with `error: (-2:Unspecified error) The function is not implemented.`. `build_env.sh`
repairs this as its last step, but **`pip install -e .` or `uv sync` breaks it again every time**
(both packages share one `cv2` directory and whichever lands last wins). The fix:

```bash
./build_env.sh --name <your env> --resume-from 5
```

## GPU (ROCm)

```bash
export TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1
```

`train_pipeline.sh` and `eval.sh` already set this.

## Video-backed vs image-backed datasets → keep `--images-not-video`

The two environments disagree about torchcodec, and a dataset has to be readable in both:

| | torch | torchcodec | video-backed datasets |
|---|---|---|---|
| host (`build_env.sh`) | 2.12.0+rocm7.14.0 | works | readable |
| training container (base image) | 2.9.1+rocm7.13.0 | **unusable** | unreadable |

The container case is a genuine ABI mismatch: torchcodec 0.11.1 needs torch 2.11 or newer. So
recording always passes `--images-not-video`, embedding frames as PNG inside the parquet, which
both sides can read. The cost is disk: about 39 KB per frame measured, so two cameras at 30 fps
is roughly 200 MB per episode and about 4 GB for 20 episodes.

One correction to an older conclusion: "torchcodec does not load, the ABI does not match" was a
misdiagnosis on the host -- the symptom there came from `LD_PRELOAD` breaking torch's ROCm chain
(previous section). Measured 2026-09-15, with `unset LD_PRELOAD` the host decodes mp4 fine and a
video-backed `LeRobotDataset` both writes and reads back. The container limitation is real
though, so the recommendation is unchanged.

## SmolVLA needs `--rename_map`, ACT does not

ACT's config lists `head`/`wrist` directly, so the pre-flight check passes. A SmolVLA finetune
only rewrites the output shapes; the input names are still the `camera1/2/3` inherited from
`smolvla_base` (even `observation.state: [6]` is stale). `build_rollout_context` compares the
config's `input_features` against the names the robot provides and blocks before the policy is
ever touched:

```
ValueError: Visual feature mismatch ... Policy expects: camera1/2/3 ... Robot provides: head/wrist
```

Inference itself is fine with `head`/`wrist` (the normalizer was built from this dataset), but
that check reads the config. `eval.sh` and `chain_eval.py` both add this map for SmolVLA only.
The same applies at training time: `--rename_map` maps `head`/`wrist` onto `camera1`/`camera2`
and `camera3` is simply left empty — `smolvla_base` expects three cameras, but the validator
accepts a subset, so there is no need to fabricate a third feed.

## Why recording does not use `lerobot-record`

`lerobot-record` needs a teleoperator config, but this machine is **two leader arms plus a
keyboard driving the head and base** — no single teleop type can express that. So recording goes
through `record_teleop.py`, which builds its own `LeRobotDataset` and assembles frames with
`build_dataset_frame`, deriving the feature definitions straight from `robot.action_features` /
`observation_features` — producing the same format `lerobot-record` would.

For the same reason `train_pipeline.sh` does the merge itself: `lerobot-train` takes one dataset
at a time, and `MultiLeRobotDataset` raises `NotImplementedError` outright in this version.

---

# Command reference

Full workflow in [README.md](README.md). Only the environment-specific caveats are listed
here.

## Viewing the cameras

```bash
./run.sh python data_collect/view_cams.py                          # both, 640x480
./run.sh python data_collect/view_cams.py --width 320 --height 240 # recommended over SSH
./run.sh python data_collect/view_cams.py --cam wrist              # one only
```

Works remotely over `ssh -X`, measured at **12.1 Hz** (29.9 Hz locally). X11 forwarding ships
uncompressed pixels — 1.8 MB per 1280×480 frame — so this is the network's ceiling, not the
machine's.

## Record

```bash
LEFT_LEADER=none RIGHT_LEADER=/dev/ttyACM2 ./record_phases.sh blue open,close
RIGHT_LEADER=none LEFT_LEADER=/dev/ttyACM2 ./record_phases.sh blue pick
./run.sh ./record_phases.sh --status        # progress across all nine datasets
./run.sh python data_collect/check_phases.py             # quality check
```

| key | action |
|---|---|
| `r` | start a cycle / press again to end it early (completed phases are kept) |
| `c` | end this phase and start the next; after the last one the whole cycle is written |
| `x` | discard the **entire cycle** (the drawer is already open, so the middle phase cannot be re-recorded on its own) |
| `w`/`s` `a`/`d` | head pitch / pan (needs `HEAD_WHEELS=1`) |
| `i`/`k` `j`/`l` | base forward/back / rotate (needs `HEAD_WHEELS=1`) |
| `n`/`m` | base speed up / down |
| `ctrl-c` | quit (a half-finished episode is still saved, nothing is wasted) |

- **The terminal must keep focus** — the keyboard is read from stdin (pynput cannot capture
  global key events under Wayland).
- `FPS` must stay consistent within a dataset — the resume check compares fps and refuses to
  continue on a mismatch rather than silently mixing them. Use the same value at eval time.
- Re-runnable to continue; the script decides whether to `--resume` on its own.
- The `cycles: N, ... faults: X` line printed at the end is that session's hardware fault count,
  which is very useful for comparing wiring arrangements.

## Train

```bash
./run.sh ./train_pipeline.sh --prep-only   # prepare and verify only, no training
./run.sh ./train_pipeline.sh               # prepare + verify + train
./run.sh ./train_pipeline.sh --train-only  # skip preparation, train directly
./run.sh ./train_pipeline.sh --status      # progress
```

The preparation stage does trim to 12 dims → merge the three pick colours → fix normalisation
stats → `verify_arms.py`, and **refuses to start training if verification fails**. All 10 models
take about 12.5 hours; intermediate checkpoints can be evaluated directly.

## Eval

```bash
./run.sh ./eval.sh blue 60             # latest checkpoint, blue drawer, 60 s
./run.sh ./eval.sh green 60 010000     # a specific checkpoint
FPS=15 HORIZON=20 ./eval.sh blue
./run.sh python eval/chain_eval.py --stages act_open_blue,smolvla_pick_all,act_close_blue \
  --colour blue --checkpoint 005000,020000,005000 --trace /tmp/chain.npz
```

- **Base safety**: the policy's output includes `x.vel`/`y.vel`/`theta.vel`. The base never
  moved during training, so those three dims are near-zero **noise** — but not zero. The default
  `HEAD_WHEELS=0` keeps those commands from reaching the wheels; if you set it to `1`, clear the
  floor and keep a hand on ctrl-c.
- Use `--inference.type=rtc` rather than `sync`: sync runs a full inference every control tick
  (about 320 ms), pinning the loop at 3 Hz. RTC computes the next chunk on a background thread.
  `HORIZON` has to make one chunk's playback longer than the inference time: 10 ticks @ 15 fps
  = 0.67 s.
- The colour determines the task string and **must match the recording word for word**.

---

# Troubleshooting

| symptom | cause |
|---|---|
| `invalid choice: 'xlerobot'` | patch 2 above not applied |
| `size of tensor a (14) must match b (17)` | patch 3 not applied |
| `Visual feature mismatch ... camera1/2/3` | SmolVLA is missing `--rename_map` |
| `imshow: The function is not implemented` | headless opencv got reinstalled |
| `Could not load libtorchcodec` | the dataset is video-based; re-record with `--images-not-video` |
| `There is no status packet` | dropped bus packets; first check no second process holds the same port |
| `observation failed: (5, ...)` + `Port is in use!` | USB adapter reset — see "hand-over current spike" below |
| `VIDIOC_REQBUFS: errno=19` | the camera vanished from the USB bus; physical problem |
| `Corrupt JPEG data` | the two cameras are in ports too close together — see below |
| loop running slower than the target fps | raise `HORIZON` or lower `--fps` |
| camera path does not exist | ports were changed; re-run `ls /dev/v4l/by-path/` |

## Multiple processes is the hardest one to recognise

Linux serial ports have no mandatory locking. Run two scripts at once and both will issue
commands on the same bus and race to read the replies. The symptom is `There is no status packet`
alternating with `Incorrect status packet`, which looks exactly like a hardware fault.

`record_phases.sh` and `eval.sh` check with `fuser` at startup and refuse to run. To check by
hand:

```bash
fuser -v /dev/ttyACM0 /dev/ttyACM1     # should print nothing
```

## A hand-over current spike resets USB, and looks exactly like a loose cable

Cause and fix are in README "Pose jump at hand-over". Recorded here is only how to **tell them
apart**:

```
[RUN] observation failed: (5, 'Input/output error')
... Failed to sync read 'Present_Position' ... [TxRxResult] Port is in use!
```

`journalctl -k` shows a real `USB disconnect` followed by re-enumeration. Replay episode 0 of
`pick_blue_arms` with `lerobot-replay` — same motion, same bus, no policy in the loop. If replay
runs clean, it is not the hardware.

---

# Physical root causes

The long-running `There is no status packet`, `Corrupt JPEG data`, repeated USB re-enumeration
and `/dev/ttyACM*` renumbering were **two independent physical problems**, not one.

## 0. Before startup: the arms must be within their calibrated range

Torque is off before `connect()`, so an arm can be pushed by hand **outside** its calibrated
range. The moment it is energised the servo clamps the goal back into
`[Min_Position_Limit, Max_Position_Limit]` and **drives it all the way back into range** — on a
gravity-loaded joint like shoulder_lift that is a forced movement of several hundred counts,
enough to trip the motor's own `Unloading_Condition` overload protection: it **drops torque and
flashes its LED**, and teleop dies on the spot.

`record_teleop.py` checks at startup and lists any joint out of range. When you see the warning,
push it back by hand.

A worked example: in one test shoulder_lift sat at 3967 against an upper limit of 3244 (723 over).
On power-up peak current hit 455 and load pegged at 1000 (other joints were 7-13), then it
unloaded and flashed. **The motor itself was completely fine** — moved back into range and
re-powered, it held statically for 8 seconds with load and current both at 0.

Ruled out along the way: leader and follower calibration are **not** inconsistent. The span ratio
of all five arm joints is 1.00x (shoulder_lift is 2367 vs 2378) and `drive_mode` is identical
throughout. Only the gripper differs, at 0.81x — for the same normalised change the follower
gripper travels about 290 counts further, i.e. it grips harder than the leader suggests. Worth
fixing, but unrelated to the overload.

## 1. MJPG corruption — the two cameras are in USB ports too close together

With both plugged into adjacent ports (or nearby ports on the same hub), MJPG frames get heavily
corrupted. libjpeg can usually resynchronise from a restart marker, so it **never raises** — it
just prints `Corrupt JPEG data` on stderr while the broken frames get written into the dataset
anyway.

**Moving the two to well-separated ports (different controllers) fixed it.**

Reference measurements (both cameras on the same built-in hub at the time):

| condition | frames | MJPG corruption |
|---|---|---|
| cameras only, robot not connected | 1346 | 0.0% |
| cameras + robot connected, torque on | 1116 | 34.0% |

At the time I attributed that difference to motor noise; that inference was wrong — the real
variable was port distance. The head is now on `pci-0000:bf:00.4` and the wrist on
`pci-0000:bd:00.4`, on separate controllers.

## 2. Motor bus disconnects — right arm shoulder_lift (id 2) is faulty

Testing one motor at a time under power gave a very clean answer:

```
id 1 shoulder_pan    moved 56/60   peak cur 11   healthy
id 2 shoulder_lift   ← the instant it is commanded under power, the USB device vanishes
id 3-6               all Input/output error (the port no longer exists)
```

The kernel log, in the same second: `usb 3-1.4: USB disconnect` → re-enumerated one second later
under a new ttyACM number.

**Driving this motor collapses the power rail** (measured previously at 12.1 V → 5.0 V) and the
USB adapter board browns out and reboots. Every serial packet drop, renumbering and
`termios.error` is downstream of this.

To tell a burnt winding from a seized mechanism: turn shoulder_lift by hand with torque off.
Turns freely = shorted winding or burnt driver stage, replace the motor; will not turn =
mechanical seizure, clear that first.

**No data can be collected until this is resolved** — any grasping motion uses shoulder_lift, and
every time it is driven the whole USB subsystem restarts.

## Hypotheses ruled out (all with data)

| hypothesis | evidence |
|---|---|
| the built-in hub is itself unstable | 5378 static ticks at 29.9 Hz, zero faults |
| one particular register is the problem | five registers, 100 sync_reads each, zero failures |
| torque being on causes interference | 150 reads while holding still, zero failures with torque on and off |
| insufficient USB bandwidth | camera fps halved, corruption rate unchanged (fixed per second, independent of throughput) |
| head and wheels participating | static A/B, zero faults in both |

---

# Where things live

| what | where |
|---|---|
| phase datasets | `~/lerobot_data/xlerobot_{open,pick,close}_{yellow,blue,green}/` |
| training copies (12-dim) | same with an `_arms` suffix, produced by `trim_dims.py` |
| merged pick set | `~/lerobot_data/xlerobot_pick_all_arms/` |
| checkpoints | `~/lerobot_out/{act_*,smolvla_pick_all}/checkpoints/` |
| training logs | `~/lerobot_out/run_<model>.log`, `train_pipeline.log` |
| calibration files | `~/.cache/huggingface/lerobot/calibration/robots/xlerobot/` |
| XLeRobot driver | `<lerobot>/src/lerobot/robots/xlerobot/` |
| container image | `auplc-bimanual`, built from `Dockerfile`, launched by `run.sh` |
| conda env (no container) | `~/miniforge3/envs/lerobot/`, snapshot in `environment.yml`, rebuilt with `build_env.sh` |
