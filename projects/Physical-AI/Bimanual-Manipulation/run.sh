#!/usr/bin/env bash
# Copyright (C) 2025 Advanced Micro Devices, Inc. All rights reserved.
# Run the lab container with everything the robot needs attached.
#
#   ./run.sh                                   # interactive shell
#   ./run.sh ./train_pipeline.sh               # train
#   ./run.sh ./record_phases.sh blue open,close
#   ./run.sh python verify_env.py              # check the environment first when something is off
#   ./run.sh --check                           # shorthand for the line above
#
# This exists because the flag list is long and every item on it fails silently or obscurely when
# it is missing:
#
#   --group-add render        /dev/kfd and /dev/dri/renderD* belong to the render group. Without
#                             it torch.cuda.is_available() is False and nothing says why.
#   --group-add dialout       /dev/ttyACM* belong to dialout. Note this works even though the
#                             host user is not in dialout -- the container's supplementary groups
#                             are set by the daemon, which is root.
#   -v /dev/serial            --device passes the character device but not the
#   -v /dev/v4l               /dev/serial/by-id and /dev/v4l/by-path symlinks, and every script
#                             here addresses hardware by those stable names, never by ttyACM0.
#   -e DISPLAY, /tmp/.X11-unix, ~/.Xauthority
#                             cv2.imshow needs an X server. Under Wayland this is XWayland, which
#                             is why DISPLAY is :0 and the socket is still in /tmp/.X11-unix.
#
# Overridable: IMAGE, LEROBOT (the patched checkout), DATA, OUT.

set -euo pipefail

IMAGE="${IMAGE:-auplc-bimanual}"
LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LEROBOT="${LEROBOT:-$HOME/lerobot}"
DATA="${DATA:-$HOME/lerobot_data}"
OUT="${OUT:-$HOME/lerobot_out}"

# The lerobot checkout lives outside this repo, so it is checked rather than assumed.
[ -d "$LEROBOT/src/lerobot" ] || {
  echo "error: $LEROBOT is not a lerobot checkout (no src/lerobot)" >&2
  echo "       run ./setup_lerobot.sh, or pass LEROBOT=/path/to/lerobot" >&2; exit 1; }
[ -d "$LEROBOT/src/lerobot/robots/xlerobot" ] || {
  echo "error: the XLeRobot driver is not in $LEROBOT/src/lerobot/robots/" >&2
  echo "       run ./setup_lerobot.sh to install the driver and the four patches" >&2; exit 1; }

mkdir -p "$DATA" "$OUT"

# -t only when there really is a terminal, so `./run.sh ...` also works from a script or CI.
ARGS=(--rm -i)
[ -t 0 ] && ARGS+=(-t)
# Two mounts, because the lab and the lerobot checkout are separate repositories: this one is a
# directory inside aup-teaching-labs, lerobot is its own clone.
ARGS+=(-v "$LAB:/workspace/lab"
      -v "$LEROBOT:/workspace/lerobot"
      -v "$DATA:/home/jovyan/lerobot_data"
      -v "$OUT:/home/jovyan/lerobot_out")

# --- GPU ---------------------------------------------------------------------------------------
for d in /dev/kfd /dev/dri; do [ -e "$d" ] && ARGS+=(--device="$d"); done
for g in video render; do
  gid=$(getent group "$g" | cut -d: -f3) && [ -n "$gid" ] && ARGS+=(--group-add "$gid")
done

# --- serial (the arms) and cameras --------------------------------------------------------------
# Passed only if present, so the container still starts with the robot unplugged -- training does
# not need any of it.
gid=$(getent group dialout | cut -d: -f3) && [ -n "$gid" ] && ARGS+=(--group-add "$gid")
for d in /dev/ttyACM*; do [ -e "$d" ] && ARGS+=(--device="$d"); done
for d in /dev/video*;  do [ -e "$d" ] && ARGS+=(--device="$d"); done
[ -d /dev/serial ] && ARGS+=(-v /dev/serial:/dev/serial:ro)
[ -d /dev/v4l ]    && ARGS+=(-v /dev/v4l:/dev/v4l:ro)

# --- X11, for cv2.imshow ------------------------------------------------------------------------
if [ -n "${DISPLAY:-}" ] && [ -d /tmp/.X11-unix ]; then
  ARGS+=(-e "DISPLAY=$DISPLAY" -v /tmp/.X11-unix:/tmp/.X11-unix:ro)
  XAUTH="${XAUTHORITY:-$HOME/.Xauthority}"
  [ -f "$XAUTH" ] && ARGS+=(-v "$XAUTH:/home/jovyan/.Xauthority:ro" -e XAUTHORITY=/home/jovyan/.Xauthority)
fi

if [ "${1:-}" = "--check" ]; then set -- python verify_env.py; fi
[ $# -eq 0 ] && set -- bash

exec docker run "${ARGS[@]}" "$IMAGE" "$@"
