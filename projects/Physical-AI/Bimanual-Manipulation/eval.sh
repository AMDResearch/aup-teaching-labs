#!/usr/bin/env bash
# Run one of the trained policies on the real robot.
#
#   conda activate lerobot
#   ./eval.sh --list                       # every checkpoint available
#   ./eval.sh smolvla/all blue             # merged SmolVLA, told to use the blue drawer
#   ./eval.sh smolvla/blue blue 120        # the blue-only SmolVLA, 120s
#   ./eval.sh act/blue blue 60 010000      # ACT, an earlier checkpoint
#   FPS=15 HORIZON=20 ./eval.sh act/green green
#   HEAD_WHEELS=1 ./eval.sh smolvla/all blue
#
# First argument is which model: <smolvla|act>/<all|yellow|blue|green> (ACT has no `all` -- it is
# not language conditioned, so a three-task dataset would give it contradictory targets).
# Second is which drawer to ask for, which becomes the task string.
#
# The interesting comparison is smolvla/all against the single-colour models on the SAME colour,
# and smolvla/all across all three colours with the cube left in one place -- that last one is
# what shows whether it reads the language at all rather than replaying one memorised motion.
#
# SAFETY: the policy also outputs base velocities, learned from sensor noise since the base never
# moved while recording. HEAD_WHEELS=0 (the default) keeps those commands away from the wheels.
set -euo pipefail

OUT_BASE="$HOME/lerobot_out"
COLOURS=(yellow blue green)

# Output directories are not all named the same way, so resolve a model spec against a few
# candidate layouts rather than assuming one.
resolve_out() {   # <kind>/<variant> -> directory, or empty
  local kind="${1%%/*}" variant="${1#*/}" d
  for d in "$OUT_BASE/${kind}_xlerobot_drawer_$variant" "$OUT_BASE/${kind}_$variant"; do
    [ -d "$d/checkpoints" ] && { echo "$d"; return; }
  done
}

if [ "${1:-}" = "--list" ]; then
  printf '%-18s %-6s %s\n' model dims checkpoints
  for d in "$OUT_BASE"/*/; do
    [ -d "$d/checkpoints" ] || continue
    name=$(basename "$d")
    kind="${name%%_*}"
    variant="${name#*_}"; variant="${variant#xlerobot_drawer_}"
    cfg="$d/checkpoints/last/pretrained_model/config.json"
    dims=$( [ -f "$cfg" ] && python -c "
import json; c=json.load(open('$cfg'))
a=c.get('output_features',{}).get('action',{}).get('shape')
print(a[0] if a else '?')" 2>/dev/null || echo '?')
    printf '%-18s %-6s %s\n' "$kind/$variant" "$dims" "$(ls "$d/checkpoints" | grep -E '^0' | tr '\n' ' ')"
  done
  exit 0
fi

MODEL="${1:?usage: ./eval.sh <smolvla|act>/<variant> <yellow|blue|green> [seconds] [checkpoint]  (or --list)
       variant is all|yellow|blue|green for a whole-task model,
       or open_<colour>|pick_<colour>|close_<colour> for a per-phase one}"
COLOUR="${2:?which drawer to ask for: yellow, blue or green}"
DURATION="${3:-60}"
CKPT="${4:-010000}"

POLICY_KIND="${MODEL%%/*}"
VARIANT="${MODEL#*/}"
case "$POLICY_KIND" in smolvla|act) ;; *)
  echo "ERROR: model must start with smolvla/ or act/ (got '$MODEL')" >&2; exit 1 ;;
esac
case " ${COLOURS[*]} " in *" $COLOUR "*) ;; *)
  echo "ERROR: colour must be yellow, blue or green (got '$COLOUR')" >&2; exit 1 ;;
esac

# A per-phase model (open_blue, pick_blue, close_blue) is trained on one segment of the task, so
# its recorded task string is not the whole-task sentence. It has to match character for character
# -- for a language-conditioned policy it is the only thing naming the goal, and for ACT it is
# inert but still has to be supplied.
PHASE="${VARIANT%%_*}"
case "$PHASE" in
  open)  TASK_TEMPLATE="open the %s drawer" ;;
  pick)  TASK_TEMPLATE="put the red cube in the %s drawer" ;;
  close) TASK_TEMPLATE="close the %s drawer" ;;
  *)     PHASE=""; TASK_TEMPLATE="put the red cube in the %s drawer" ;;
esac

# Read the rate from the dataset this checkpoint was trained on rather than hardcoding one. The
# policy learned the displacement between adjacent frames, so replaying at another rate rescales
# the whole trajectory: the arm reaches the same poses at the wrong speed and grasp timing drifts.
# These datasets are 15 fps; getting this wrong by 2x is silent and ruins the rollout.
if [ -n "$PHASE" ]; then
  TRAIN_ROOT="$HOME/lerobot_data/xlerobot_${VARIANT}_arms"
else
  TRAIN_ROOT="$HOME/lerobot_data/xlerobot_drawer_${VARIANT}"
fi
DATASET_FPS=$(python -c "
import json,sys
try: print(json.load(open('$TRAIN_ROOT/meta/info.json'))['fps'])
except Exception: print('')
" 2>/dev/null)
FPS="${FPS:-${DATASET_FPS:-15}}"
if [ -n "$DATASET_FPS" ] && [ "$FPS" != "$DATASET_FPS" ]; then
  echo "NOTE: running at ${FPS} fps but the training data is ${DATASET_FPS} fps --" >&2
  echo "      the trajectory will play $(python -c "print(f'{$FPS/$DATASET_FPS:.2g}')")x speed" >&2
fi
HEAD_WHEELS="${HEAD_WHEELS:-0}"
CAMERA_FPS="${CAMERA_FPS:-$FPS}"
# One chunk plays out while the next is computed. The horizon has to cover more wall-clock than a
# single inference (~320 ms for SmolVLA) and no more than the policy's n_action_steps
# (SmolVLA 50, ACT 100).
HORIZON="${HORIZON:-10}"

OUT=$(resolve_out "$MODEL")
[ -n "$OUT" ] || { echo "ERROR: no output directory for '$MODEL'" >&2
                   echo "  see what exists:  ./eval.sh --list" >&2; exit 1; }
POLICY="$OUT/checkpoints/$CKPT/pretrained_model"
# Must match the recorded string character for character -- for SmolVLA it is the only thing
# saying which drawer to aim for.
# shellcheck disable=SC2059
TASK=$(printf "$TASK_TEMPLATE" "$COLOUR")

# A policy trained on the arms-only dataset outputs 12 channels; the default robot advertises 17.
# lerobot-rollout maps policy output onto action_features, so the widths have to agree -- read the
# width from the checkpoint rather than making the caller remember which dataset it came from.
ACTION_DIM=$(python -c "
import json
c=json.load(open('$POLICY/config.json'))
a=c.get('output_features',{}).get('action',{}).get('shape')
print(a[0] if a else '')
" 2>/dev/null)
case "$ACTION_DIM" in
  12) ARMS_ONLY=(--robot.arms_only_features=true) ;;
  17|'') ARMS_ONLY=() ;;
  *) echo "ERROR: checkpoint outputs $ACTION_DIM channels; expected 12 or 17" >&2; exit 1 ;;
esac

[ -d "$POLICY" ] || {
  echo "ERROR: no checkpoint at $POLICY" >&2
  echo "  see what exists:  ./eval.sh --list" >&2
  exit 1
}

# Serial ports are addressed by the adapter's own serial number, not by /dev/ttyACM*. cdc_acm hands
# out the lowest free minor at enumeration time, so a device that drops off the bus and returns can
# take a different number -- this rig has been seen swapping the two arms between one run and the
# next. Passing ttyACM0 after that drives the wrong arm, silently and with no error. Re-derive these
# Replaced an adapter, or the arms act exchanged? `python common/find_ports.py` identifies them.
# cdc_acm hands out the lowest free minor at enumeration time, so these numbers are not a property
# of the device: an adapter that drops and re-enumerates can take the other arm's number, and
# nothing raises -- lerobot's handshake only checks the ids it wants are present, and a 9-motor
# bus satisfies a request for 8, so every joint command lands on the wrong motor. If the arms
# behave as though exchanged, re-plug, or run `python common/find_ports.py` which tells them
# apart by motor count (8 = left arm + head, 9 = right arm + wheels) and can export both:
#   eval "$(python common/find_ports.py --export)"
PORT1="${PORT1:-/dev/ttyACM0}"   # left arm + head    (8 motors)
PORT2="${PORT2:-/dev/ttyACM1}"   # right arm + wheels (9 motors)
BY="/dev/v4l/by-path"
HEAD_CAM="${HEAD_CAM:-$BY/pci-0000:bf:00.4-usb-0:1:1.0-video-index0}"
WRIST_CAM="${WRIST_CAM:-$BY/pci-0000:bd:00.4-usb-0:1.3:1.0-video-index0}"

for dev in "$PORT1" "$PORT2" "$HEAD_CAM" "$WRIST_CAM"; do
  [ -e "$dev" ] || { echo "ERROR: missing $dev" >&2; exit 1; }
done
# Serial ports are not exclusive on Linux, and a second reader steals status packets in a way that
# looks exactly like a hardware fault.
if command -v fuser >/dev/null && fuser "$PORT1" "$PORT2" >/dev/null 2>&1; then
  echo "ERROR: another process already has the follower buses open:" >&2
  fuser -v "$PORT1" "$PORT2" >&2
  exit 1
fi

# SmolVLA needs a rename map here; ACT does not.
#
# ACT was trained on these exact keys, so its config lists head/wrist and the check passes.
# SmolVLA's config still lists camera1/2/3 -- stale fields inherited from smolvla_base that the
# finetune never rewrote. Inference itself is fine with head/wrist (the normalizer was built from
# this dataset and keys off those names), but build_rollout_context compares the *config's*
# input_features against what the robot provides and refuses before ever reaching the policy:
#   ValueError: Visual feature mismatch ... Policy expects: camera1/2/3 ... Robot provides: head/wrist
# The map satisfies that check. camera3 goes unfilled and SmolVLA pads it, as in training.
if [ "$POLICY_KIND" = smolvla ]; then
  RENAME=(--rename_map='{
    "observation.images.head": "observation.images.camera1",
    "observation.images.wrist": "observation.images.camera2"
  }')
else
  RENAME=()
fi

CAMS=$(cat <<EOF
{
  "head":  {"type":"opencv","index_or_path":"$HEAD_CAM","width":640,"height":480,"fps":$CAMERA_FPS,"fourcc":"MJPG"},
  "wrist": {"type":"opencv","index_or_path":"$WRIST_CAM","width":640,"height":480,"fps":$CAMERA_FPS,"fourcc":"MJPG"}
}
EOF
)

# See SETUP.md: this torch (2.12.0+rocm7.14.0, AMD rocm-sdk wheels) must NOT have the system HSA
# runtime preloaded -- that was a workaround for the older build and now breaks it.
export TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1

echo "=== eval  $MODEL  checkpoint $CKPT  ->  \"$TASK\" ==="
if [ "$POLICY_KIND" = smolvla ]; then
  echo "    ${DURATION}s @ ${FPS}fps, rtc inference, horizon $HORIZON"
else
  echo "    ${DURATION}s @ ${FPS}fps, sync inference (ACT does not support rtc)"
fi
echo "    policy -> $POLICY  (${ACTION_DIM:-17} action channels)"
MODEL_COLOUR="${VARIANT##*_}"
if [ "$POLICY_KIND" = act ] && [ "$MODEL_COLOUR" != "$COLOUR" ]; then
  echo "    NOTE: ACT ignores the task string, so act/$VARIANT will do the $MODEL_COLOUR drawer"
  echo "          regardless of asking for $COLOUR. Useful only as a negative control."
elif [ "$VARIANT" != all ] && [ "$MODEL_COLOUR" != "$COLOUR" ]; then
  echo "    NOTE: $MODEL only ever saw the $VARIANT drawer; asking for $COLOUR tests whether it"
  echo "          follows language it was never trained to vary."
fi
if [ "$HEAD_WHEELS" = "0" ]; then
  echo "    head+wheels: off -- the policy's base commands go nowhere"
else
  echo "    head+wheels: ON -- keep the floor clear, the policy drives the wheels"
fi

# RTC for SmolVLA, sync for ACT.
#
# RTC computes the next action chunk in a background thread, which is what makes SmolVLA usable at
# all: sync calls the policy once per control tick and a single SmolVLA forward is ~320 ms, capping
# the loop near 3 Hz. But RTC only works with policies whose predict_action_chunk() accepts an
# inference_delay -- the flow-matching VLAs. ACT's does not:
#   TypeError: ACTPolicy.predict_action_chunk() got an unexpected keyword argument 'inference_delay'
# ACT does not need it anyway: it is a far smaller model, so one forward per tick fits easily
# inside a 15 fps budget.
if [ "$POLICY_KIND" = smolvla ]; then
  INFER=(--inference.type=rtc --inference.rtc.execution_horizon="$HORIZON")
else
  INFER=(--inference.type=sync)
fi

# ACT ships with chunk_size == n_action_steps == 100: it computes 100 actions from one observation
# and executes all of them before looking again. --inference.type=sync does not change that, since
# the queue lives inside ACTPolicy.select_action. At 15 fps that is 6.7s of open-loop motion, which
# on a 12s task means the rollout runs off two glances at the scene. Unset by default so this
# behaves exactly as it always has; set ACT_STEPS=1 for closed loop (measured at 47ms per forward,
# inside the 67ms budget), or a small number for a compromise.
ACT_STEPS="${ACT_STEPS:-}"
NAS=()
if [ -n "$ACT_STEPS" ]; then
  NAS=(--policy.n_action_steps="$ACT_STEPS")
  echo "    n_action_steps=$ACT_STEPS (closed loop)"
fi

lerobot-rollout \
  --strategy.type=base \
  "${INFER[@]}" \
  --policy.path="$POLICY" \
  --policy.device=cuda \
  "${NAS[@]}" \
  --robot.type=xlerobot \
  --robot.port1="$PORT1" --robot.port2="$PORT2" \
  --robot.use_head_wheels="$([ "$HEAD_WHEELS" = "0" ] && echo false || echo true)" \
  "${ARMS_ONLY[@]}" \
  --robot.cameras="$CAMS" \
  "${RENAME[@]}" \
  --task="$TASK" \
  --fps="$FPS" --duration="$DURATION"
