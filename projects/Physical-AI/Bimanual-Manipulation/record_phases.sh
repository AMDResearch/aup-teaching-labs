#!/usr/bin/env bash
# Record the drawer task as separate per-arm phases, one dataset per (phase, colour) -- nine in
# all: open/close x 3 colours driven by the right arm, pick x 3 colours driven by the left arm.
#
#   conda activate lerobot
#
#   # session 1: right leader plugged in, records open + close in one continuous motion
#   LEFT_LEADER=none RIGHT_LEADER=/dev/ttyACM2 ./record_phases.sh blue open,close
#
#   # session 2: swap the leader over, drawer already open, records the pick-and-place
#   RIGHT_LEADER=none LEFT_LEADER=/dev/ttyACM2 ./record_phases.sh blue pick
#
#   ./record_phases.sh --status                # episode counts for all nine datasets
#   FPS=15 ./record_phases.sh blue open,close  # 15 is already the default
#   TARGET_OPEN_CLOSE=10 TARGET_PICK=18         # the defaults; only affect the printed targets
#
# Only one leader needs to be connected. The other arm is still read and recorded, held at the
# pose it started in, and its action channels are constant -- trim_dims.py --keep right|left drops
# them when building the per-arm training sets. So the recording is 17-wide like every other
# dataset here and the decision about how many policies to train stays open.
#
# WHY open AND close IN ONE SESSION: they are cut out of one physical demonstration -- you open the
# drawer, press `c` the moment it is open, then close it, press `c` again. The close data therefore
# starts from exactly the state the open data ends in, which is the state the `open` policy will
# actually hand over at run time. Recording close as its own session, from a drawer opened by hand,
# would train it on hand-off states the open policy never produces, and that mismatch is what
# breaks chained policies. The same argument does not reach across the leader swap, so `pick` is
# recorded from a drawer opened by hand -- keep that opened position consistent with where the
# `open` demonstrations end.
#
# Nothing is written until a cycle completes, so `x` throws the whole cycle away. Deliberate: once
# the drawer is open you cannot redo just the `open` phase, so a botched phase means redoing both.
#
#   r    start a cycle          (press again to end it early, keeping the finished phases)
#   c    finish this phase, start the next; after the last one the cycle is written
#   x    discard the whole cycle
#   w/s a/d  head tilt/pan      i/k j/l  base drive/rotate
#   ctrl-c   stop (a cycle in progress is written, not lost)
#
# Resumable: run it as many times as you like, it appends to whatever is already there.
set -euo pipefail

ROOT_BASE="${ROOT_BASE:-$HOME/lerobot_data}"
ALL_PHASES="open,pick,close"
COLOURS=(yellow blue green)

# Target episodes per (phase, colour), and why they differ.
#
# open/close: the drawer never moves, so every demonstration traces nearly the same trajectory --
# there is no object pose to cover and little for extra episodes to add beyond what varying the
# arm's start pose already gives. Ten is enough for a per-colour ACT model.
#
# pick: the cube is placed on a 3x3 grid, so the episode count has to cover positions, not just
# repeat a motion. Ten would leave each of the nine cells seen exactly once, with no redundancy if
# one is botched -- 18 gives two per cell. Cut this last.
target_for() {
  case "$1" in
    pick) echo "${TARGET_PICK:-18}" ;;
    *)    echo "${TARGET_OPEN_CLOSE:-10}" ;;
  esac
}

if [ "${1:-}" = "--status" ]; then
  printf '%-28s %s\n' dataset episodes
  for colour in "${COLOURS[@]}"; do
    for phase in ${ALL_PHASES//,/ }; do
      root="$ROOT_BASE/xlerobot_${phase}_${colour}"
      [ -f "$root/meta/info.json" ] || continue
      n=$(python -c "import json; print(json.load(open('$root/meta/info.json'))['total_episodes'])")
      printf '%-28s %s/%s\n' "${phase}_${colour}" "$n" "$(target_for "$phase")"
    done
  done
  exit 0
fi

COLOUR="${1:?usage: ./record_phases.sh <yellow|blue|green> [phases]   (or --status)}"
PHASES="${2:-$ALL_PHASES}"
case " ${COLOURS[*]} " in *" $COLOUR "*) ;; *)
  echo "ERROR: colour must be yellow, blue or green (got '$COLOUR')" >&2; exit 1 ;;
esac

FPS="${FPS:-15}"
# Both cameras share one USB 2.0 hub; capturing frames the loop never reads doubles the bus
# traffic for nothing and shows up as "Corrupt JPEG data".
CAMERA_FPS="${CAMERA_FPS:-$FPS}"

# The head and wheels take no part in this task, so leave them un-energised. They are still read,
# so the observation stays 17-wide; trim_dims.py cuts it to the 12 arm channels before training.
HEAD_WHEELS="${HEAD_WHEELS:-0}"
[ "$HEAD_WHEELS" = "0" ] && ARMS_ONLY=(--no-head-wheels) || ARMS_ONLY=()

CALIB_LEADERS="${CALIB_LEADERS:-0}"
[ "$CALIB_LEADERS" = "1" ] && CALIB=(--calibrate-leaders) || CALIB=()

# Serial ports are addressed by the adapter's own serial number, not by /dev/ttyACM*. cdc_acm hands
# out the lowest free minor at enumeration time, so a device that drops off the bus and returns can
# take a different number -- this rig has been seen swapping the two arms between one run and the
# next. Passing ttyACM0 after that drives the wrong arm, silently and with no error. Re-derive these
# from /dev/serial/by-id/ if an adapter is ever replaced (8 motors = left arm + head, 9 = right + wheels).
PORT1="${PORT1:-/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B14034094-if00}"   # left arm + head
PORT2="${PORT2:-/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B14115158-if00}"   # right arm + wheels
# Leader arms, one per follower arm. Set either to `none` to leave that arm unteleoperated -- it
# is still read and recorded, it just does not move. Useful when only one leader is plugged in and
# only the phases driven by that arm are being recorded.
LEFT_LEADER="${LEFT_LEADER:-/dev/ttyACM2}"
RIGHT_LEADER="${RIGHT_LEADER:-/dev/ttyACM3}"
[ "$LEFT_LEADER"  = none ] && LEFT_LEADER=""
[ "$RIGHT_LEADER" = none ] && RIGHT_LEADER=""
if [ -z "$LEFT_LEADER" ] && [ -z "$RIGHT_LEADER" ]; then
  echo "ERROR: both leaders disabled -- nothing would move" >&2; exit 1
fi

# by-path, NOT by-id: both cameras report an identical serial so by-id collapses them into one
# symlink, and /dev/videoN renumbers on every replug.
BY="/dev/v4l/by-path"
HEAD_CAM="${HEAD_CAM:-$BY/pci-0000:bf:00.4-usb-0:1:1.0-video-index0}"
WRIST_CAM="${WRIST_CAM:-$BY/pci-0000:bd:00.4-usb-0:1.3:1.0-video-index0}"

for dev in "$PORT1" "$PORT2" "$LEFT_LEADER" "$RIGHT_LEADER" "$HEAD_CAM" "$WRIST_CAM"; do
  [ -n "$dev" ] || continue          # a disabled leader
  [ -e "$dev" ] || { echo "ERROR: missing $dev" >&2
                     echo "  cameras: ls $BY/*-usb-*-video-index0" >&2; exit 1; }
done

# Serial ports are not exclusive on Linux: a second reader steals status packets from the first
# and the symptoms look exactly like a hardware fault ("There is no status packet").
if command -v fuser >/dev/null && fuser "$PORT1" "$PORT2" >/dev/null 2>&1; then
  echo "ERROR: another process already has the follower buses open:" >&2
  fuser -v "$PORT1" "$PORT2" >&2
  exit 1
fi

# --resume unconditionally: record_teleop.py decides per phase from what is on disk, so the first
# session creates the datasets and later ones append. A directory holding only info.json is the
# residue of a run aborted at the calibration prompt and cannot be resumed onto -- clear those.
for phase in ${PHASES//,/ }; do
  root="$ROOT_BASE/xlerobot_${phase}_${COLOUR}"
  if [ -d "$root" ] && [ ! -f "$root/meta/tasks.parquet" ]; then
    if [ -n "$(find "$root" -name '*.parquet' -print -quit)" ]; then
      echo "ERROR: $root holds data but no meta/tasks.parquet -- inspect it by hand" >&2
      exit 1
    fi
    echo "note: clearing empty leftover dataset at $root"
    rm -rf "$root"
  fi
done

echo "=== recording [$COLOUR]  phases: ${PHASES//,/ -> }  |  loop ${FPS}fps  cams ${CAMERA_FPS}fps ==="
for phase in ${PHASES//,/ }; do
  root="$ROOT_BASE/xlerobot_${phase}_${COLOUR}"
  n=$( [ -f "$root/meta/info.json" ] \
       && python -c "import json; print(json.load(open('$root/meta/info.json'))['total_episodes'])" \
       || echo 0 )
  printf '    %-6s %2s/%s episodes  -> %s\n' "$phase" "$n" "$(target_for "$phase")" "$root"
done
echo "    head+wheels: $([ "$HEAD_WHEELS" = "0" ] && echo "off (arms only)" || echo "on")"
echo "    leaders: left=${LEFT_LEADER:-disabled}  right=${RIGHT_LEADER:-disabled}"
echo "    r start cycle    c next phase    x discard cycle    ctrl-c stop"

# --no-viz: images go to the OpenCV window, not rerun. The rerun viewer cannot keep up with two
# camera streams here and back-pressures the SDK, blocking the control loop for seconds at a time
# -- which distorts the timestamps of everything recorded.
# --images-not-video: torchcodec cannot load against this ROCm torch, so a video dataset records
# fine but cannot be read back.
python -u "$(dirname "$0")/record_teleop.py" \
  --port1="$PORT1" --port2="$PORT2" \
  --left-leader="$LEFT_LEADER" --left-leader-id=left_leader \
  --right-leader="$RIGHT_LEADER" --right-leader-id=right_leader \
  --head-camera="$HEAD_CAM" \
  --wrist-camera="$WRIST_CAM" \
  --fps="$FPS" \
  --camera-fps="$CAMERA_FPS" \
  --no-viz \
  --record \
  "${ARMS_ONLY[@]}" \
  "${CALIB[@]}" \
  --phases="$PHASES" \
  --colour="$COLOUR" \
  --root-base="$ROOT_BASE" \
  --images-not-video \
  --resume

echo "=== done ==="
for phase in ${PHASES//,/ }; do
  root="$ROOT_BASE/xlerobot_${phase}_${COLOUR}"
  n=$(python -c "import json; print(json.load(open('$root/meta/info.json'))['total_episodes'])")
  printf '    %-6s %2s/%s episodes\n' "$phase" "$n" "$(target_for "$phase")"
done
