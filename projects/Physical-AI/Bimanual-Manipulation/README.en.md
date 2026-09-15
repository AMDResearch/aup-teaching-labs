[中文](README.md) | **English**

# XLeRobot — cube to drawer, as a chain of per-arm policies

Using [LeRobot](https://github.com/huggingface/lerobot) on a dual-arm XLeRobot to do
"open the drawer → put the red cube in → close the drawer", split into three phases,
trained separately, and chained together on the real robot.

Runs on an **AMD GPU (Radeon 8060S / gfx1151)**.

> **This repo does not work on its own.** The XLeRobot driver these scripts import is not in
> upstream lerobot — see [`lerobot_patches/`](lerobot_patches/) and follow the instructions there
> to install it into your own lerobot checkout. Baseline is commit `95211b98`.
> Environment rebuild and every trap hit along the way: [SETUP.en.md](SETUP.en.md).

---

## Architecture

The whole task is a relay between **two arms**, and only one of them moves at a time:

```
right: open  →  left: pick  →  right: close
```

### The key recording decision

`open` and `close` are **recorded inside one continuous demonstration**, switching with `c`
mid-way — not as two separate recordings. The `close` data has to continue from the state
`open` ended in, because that is the state the `open` policy actually hands over at run time.

### What happens to the idle arm

Every recording contains **both arms** (only one leader is connected; the other arm stays put).
So a 12-dim policy reads the idle arm's pose in as a constant and emits it back as a constant —
neither of which is a decision.

`chain_eval.py` therefore **masks** each phase: the idle arm's input is substituted with the
fixed pose it held during training, and its output is discarded so it holds position. This is
equivalent to what you would get by slicing the data down to a single 6-dim arm, without having
to make the robot advertise only 6 channels.

---

## Current pipeline

```bash
./build_env.sh
conda activate lerobot && cd ~/lerobot/xlerobot_drawer

./record_phases.sh blue open,close          # right leader: record open + close in one go
RIGHT_LEADER=none LEFT_LEADER=... \
  ./record_phases.sh blue pick              # swap to the left leader: record pick
python check_phases.py                      # data quality
./train_pipeline.sh                         # prepare + verify + train 10 models
python chain_eval.py --stages act_open_blue,smolvla_pick_all,act_close_blue \
  --colour blue --checkpoint 005000,020000,005000
```

### Environment: three steps, then everything goes through `run.sh`

This lab lives inside `aup-teaching-labs`; lerobot is a separate clone kept outside the repo and
mounted into the container at run time. They are separate because XLeRobot is not upstream --
that checkout has to be modified (the driver plus four patches), and keeping it outside means you
can read and edit the lerobot source you are actually running.

```bash
# 1. this repo
git clone https://github.com/amd-weiliao/aup-teaching-labs.git ~/aup-teaching-labs
cd ~/aup-teaching-labs/projects/Physical-AI/Bimanual-Manipulation

# 2. prepare lerobot (clones to ~/lerobot, adds the driver, applies the four patches; re-runnable)
./setup_lerobot.sh
./setup_lerobot.sh --check      # report the state, change nothing

# 3. build the image
docker build -t auplc-bimanual .
```

After that, recording, training and evaluation all happen in the same container:

```bash
./run.sh --check                             # verify the environment first, eight checks
./run.sh ./record_phases.sh blue open,close  # record
./run.sh ./train_pipeline.sh                 # train
./run.sh python chain_eval.py --stages ... --colour blue   # evaluate
./run.sh                                     # interactive shell
```

The image holds **no lab code and no lerobot source**, only the dependencies. Two directories are
mounted in at run time:

| host | container |
|---|---|
| this lab directory | `/workspace/lab` (the working directory) |
| `~/lerobot` (override with `LEROBOT=`) | `/workspace/lerobot` |
| `~/lerobot_data`, `~/lerobot_out` | the same names under `/home/jovyan/` |

`PYTHONPATH=/workspace/lerobot/src` puts the mounted source ahead of the copy installed in the
image, so the patched code is what runs, and edits to either repository on the host take effect
without a rebuild.

### Why `run.sh` exists

The flag list is long, and every item on it fails silently or obscurely when it is missing:

| flag | what happens without it |
|---|---|
| `--group-add render` | `/dev/kfd` and `/dev/dri/renderD*` belong to the render group; without it `torch.cuda.is_available()` is False and nothing says why |
| `--group-add dialout` | `/dev/ttyACM*` belong to dialout, and the serial ports cannot be opened |
| `-v /dev/serial`, `-v /dev/v4l` | `--device` passes the character device but not the by-id / by-path symlinks, and every script addresses hardware by those stable names rather than by `ttyACM0` |
| `-e DISPLAY`, `-v /tmp/.X11-unix` | `cv2.imshow` has no X server (under Wayland this is XWayland, which is why `DISPLAY` is `:0`) |

Devices are attached only when they exist, so the container still starts with the robot
unplugged; training needs none of them.

### Three details, all measured

**The container works around the host's dialout problem.** A container's supplementary groups are
set by the daemon, which is root, so the host user does not have to be in the group: even where
you cannot open `/dev/ttyACM0` on the host, it is read-write inside. To fix the host side too,
`sudo usermod -aG dialout $USER` and log in again.

**The mounts need no chown.** The host uid is 1000 and the container's `jovyan` is also 1000, so
datasets read and checkpoints written back line up.

**`--images-not-video` is still required.** The base image ships torch 2.9.1 and torchcodec
0.11.1 needs 2.11 or newer, so the container cannot read video-backed datasets.

### Data

| | episodes each | driven arm | notes |
|---|---|---|---|
| `open_{yellow,blue,green}` | 10 | right | drawer is fixed, trajectories are nearly identical, no need for more |
| `close_{yellow,blue,green}` | 10 | right | recorded in the same cycle as `open` |
| `pick_{yellow,blue,green}` | 18 | left | cube on a 3x3 grid, 2 per cell |
| `pick_all` | 54 | left | three colours merged, all 3 task strings preserved |

Recording is 17-dim (including 2 head + 3 base velocity). `trim_dims.py` cuts it to 12-dim
(both arms) before training.

### Models

| | architecture | steps | notes |
|---|---|---|---|
| `act_{open,close,pick}_{yellow,blue,green}` | ACT | 5000 | 9 of them, checkpoint every 1000 |
| `smolvla_pick_all` | SmolVLA | 20000 | language-conditioned, checkpoint every 5000 |

`act_pick_*` and `smolvla_pick_all` are **two takes on the same phase**, directly comparable:
three specialists each shown 18 single-colour demonstrations, against one generalist shown all
54 but required to read the instruction.

---

## Files

### Environment and running

| file | purpose |
|---|---|
| `setup_lerobot.sh` | prepares the lerobot checkout: clones it at the pinned commit, installs the XLeRobot driver, applies the four patches (`--check` only reports) |
| `Dockerfile` | the container environment. Holds no lab code and no lerobot source; both are mounted at run time |
| `run.sh` | starts the container with the right GPU / serial / camera / X11 flags (`--check` verifies the environment first) |
| `verify_env.py` | eight environment checks: torch is a ROCm build, the GPU actually computes, the installed dependencies match the source, the patches took. Called automatically by `train_pipeline.sh` and `docker build` |
| `environment.yml` · `build_env.sh` | the older path that builds a conda env directly on the host, needed only when not using the container (see SETUP.en.md) |

### Recording

| file | purpose |
|---|---|
| `record_phases.sh` | phase recording — one continuous demonstration written into several datasets (`r` start, `c` cut phase, `x` discard the whole cycle) |
| `record_teleop.py` | teleop + recording engine (two leaders + keyboard for head/base, live OpenCV view) |
| `stdin_keyboard.py` | reads the keyboard from stdin under Wayland, replacing the broken pynput path |
| `check_phases.py` | quality check on the phase datasets: episode counts, dimensions, constant channels, cameras |
| `delete_episodes.py` | delete episodes and renumber |

### Training

| file | purpose |
|---|---|
| `run.sh` | starts the container with the right GPU / serial / camera / X11 flags (`--check` verifies the environment first) |
| `verify_env.py` | environment check: torch is a ROCm build, the GPU actually computes, the patches took (called automatically by `train_pipeline.sh` and `docker build`) |
| `train_pipeline.sh` | **main entry point**: trim → merge → fix stats → verify → train 10 models |
| `trim_dims.py` | 17 → 12 dims (or `--keep left/right` for a single 6-dim arm) |
| `fix_constant_stats.py` | fix normalisation stats for constant channels (**without this step training fails silently**) |
| `verify_arms.py` | verify the data before training and refuse to start if it fails (called directly by `train_pipeline.sh`) |

### Evaluation

| file | purpose |
|---|---|
| `chain_eval.py` | **multi-phase chaining**: connect once, swap only the policy, automatic hand-over, masking, ramp transition |
| `eval.sh` | single-phase evaluation via `lerobot-rollout` (matching the official path) |

### Cameras

| file | purpose |
|---|---|
| `view_cams.py` | independent preview of both cameras (check framing, focus, lighting) |

### Installed into lerobot itself

| file | purpose |
|---|---|
| `lerobot_patches/xlerobot/` | XLeRobot driver (two SO-101 arms + head + 3-wheel base), goes into `src/lerobot/robots/` |
| `lerobot_patches/*.patch` | four patches: make the official CLI recognise this robot, and reconcile the state dimensions between record and rollout |

---

## What `chain_eval.py` does

At every hand-over:

1. **Ramp transition** — walk to the next phase's demonstrated start pose, with the duration
   derived from the distance (20 units/s). Only the joints that phase drives are moved.
2. **Masking** — the idle arm's input is substituted with the training constant, its output
   discarded so it holds position.
3. **Reset** — the new policy does not inherit the previous phase's action chunk.

```
ENTER  start      c  end this phase early      h  freeze (press again to resume)      x  abort
```

Auto-advance is on by default and is **counted in ticks, not seconds**: a chunked policy's
trajectory advances one slot per call, and ACT takes about 34 ms (15 Hz) while SmolVLA takes
about 320 ms (3 Hz). Timing in seconds would cut the SmolVLA phase off a quarter of the way
through, which looks like a model failure but is really the timer firing first.

| option | default | notes |
|---|---|---|
| `--colour` | inferred from the model name | required for language-conditioned models (`smolvla_pick_all` has no colour in its name) |
| `--checkpoint` | `last` | a single value, or one per phase (`005000,020000,005000`) |
| `--auto` | `demo` | tick budget from the demonstration length; or give seconds; `off` for manual |
| `--ramp-speed` | 20 | hand-over ramp speed, `0` disables it (then expect a current spike) |
| `--n-action-steps` | 0 | keep whatever the checkpoint was trained with (100 for ACT). **Do not set it to 1** — re-planning every tick lets an out-of-distribution observation sustain itself and the arm locks up in place |
| `--max-step` | 0 | per-tick displacement cap. **Off by default** — Feetech torque is proportional to (goal − present), so capping displacement caps torque and the arm can no longer pull against the drawer's resistance |
| `--trace` | — | per-tick record written as `.npz` (commanded / observed / timestamp) |

---

## Issues

### 1. Why three phases and two architectures

SmolVLA performs poorly on long-horizon tasks. Training on the full
`right: open → left: pick → right: close` motion as a single data sample lets the two arms'
movements interfere with each other. So it is split into three smaller tasks: during
`open`/`close` the cabinet is fixed and the arm trajectory is fairly consistent, so ACT is used;
during `pick` the target cube can appear in different positions and more generalisation is
needed, so SmolVLA is used.

### 2. Constant channels destroy MEAN_STD normalisation

Only one arm is teleoperated, so the other arm's channels never move at all. The numerator of
`(x − mean) / (std + 1e-8)` is a float32 rounding residue, and dividing it by 1e-8 turns it into
a constant target on the order of **10 to 100**.

Training looks completely normal — loss falls smoothly from 0.68 to 0.131, as good as a model
that actually works. But the largest gradient signal in the entire run is that meaningless
constant, and the resulting model does not look at its inputs: on the robot it commanded
**0.2** units of travel over 104 ticks, where the demonstration covers **344**.

`fix_constant_stats.py` sets the std of those channels to 1.0. The criterion is the **raw range
of variation** being < 1 unit, not the std — one joint had std 0.0127 and slipped past the std
threshold while only actually moving 0.18 units. After the fix the mean |z| drops from 1.67 to
0.44, and the same architecture is usable after 1000 steps.

`verify_arms.py` blocks any dataset that has not been fixed.

### 3. Pose jump at hand-over

See "The key recording decision" above. A 48.7-unit step command → current spike → USB adapter
reset → EIO. The symptoms are identical to a loose cable. Solved by `chain_eval.py`'s ramp
plus masking.
