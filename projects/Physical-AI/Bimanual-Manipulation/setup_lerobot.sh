#!/usr/bin/env bash
# Prepare the lerobot checkout this lab runs against. Idempotent -- safe to re-run.
#
#   ./setup_lerobot.sh                    # clone/patch ~/lerobot
#   ./setup_lerobot.sh ~/somewhere/else
#   ./setup_lerobot.sh --check            # report the state, change nothing
#
# XLeRobot is not upstream. Two things have to happen to a plain lerobot checkout before any of
# this works: the driver goes into src/lerobot/robots/, and four patches teach the official CLI
# that the robot exists. lerobot_patches/README.md explains each one.
#
# The checkout stays OUTSIDE this repo and is mounted into the container at run time, so you can
# read and edit the lerobot source you are actually running.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REF="${LEROBOT_REF:-95211b98}"          # keep in step with the Dockerfile's LEROBOT_REF
REPO="${LEROBOT_REPO_URL:-https://github.com/huggingface/lerobot.git}"

CHECK=0
[ "${1:-}" = "--check" ] && { CHECK=1; shift; }
LEROBOT="${1:-${LEROBOT:-$HOME/lerobot}}"

say() { printf '  %-28s %s\n' "$1" "$2"; }

state() {
  [ -d "$LEROBOT/src/lerobot" ] || { say "checkout" "missing"; return 1; }
  say "checkout" "$LEROBOT ($(git -C "$LEROBOT" rev-parse --short HEAD 2>/dev/null || echo 'not a git repo'))"
  [ -d "$LEROBOT/src/lerobot/robots/xlerobot" ] && say "driver" "installed" || { say "driver" "MISSING"; return 1; }
  # A patch that is already applied fails to apply but reverses cleanly; that is the test.
  local missing=0
  for p in "$HERE"/lerobot_patches/*.patch; do
    git -C "$LEROBOT" apply --reverse --check "$p" 2>/dev/null || { say "patch $(basename "$p")" "NOT applied"; missing=1; }
  done
  [ "$missing" = 0 ] && say "patches" "all 4 applied"
  return "$missing"
}

if [ "$CHECK" = 1 ]; then
  state && { echo; echo "ready"; exit 0; } || { echo; echo "not ready -- run ./setup_lerobot.sh"; exit 1; }
fi

if [ ! -d "$LEROBOT/.git" ]; then
  echo "=== cloning $REPO -> $LEROBOT ==="
  git clone --filter=blob:none "$REPO" "$LEROBOT"
fi

echo "=== checking out $REF ==="
git -C "$LEROBOT" fetch --depth=1 origin "$REF" 2>/dev/null || git -C "$LEROBOT" fetch origin
git -C "$LEROBOT" checkout "$REF"

echo "=== XLeRobot driver -> src/lerobot/robots/xlerobot ==="
cp -r "$HERE/lerobot_patches/xlerobot" "$LEROBOT/src/lerobot/robots/"

echo "=== patches ==="
for p in "$HERE"/lerobot_patches/*.patch; do
  n="$(basename "$p")"
  if git -C "$LEROBOT" apply --reverse --check "$p" 2>/dev/null; then
    say "$n" "already applied"
  elif git -C "$LEROBOT" apply "$p" 2>/dev/null; then
    say "$n" "applied"
  else
    say "$n" "FAILED -- upstream moved; see lerobot_patches/README.md, each patch is a few lines"
    exit 1
  fi
done

echo
state
echo
echo "next:  docker build -t auplc-bimanual $HERE"
echo "       $HERE/run.sh --check"
