#!/usr/bin/env bash
# Rebuild the conda environment this repo runs in, from environment.yml.
#
#   ./build_env.sh                      # create "lerobot" from ./environment.yml
#   ./build_env.sh --name something     # ... under another name, leaving "lerobot" alone
#   ./build_env.sh --force              # tear down an existing env of that name first
#   ./build_env.sh --resume-from 2      # continue a failed run at step N (1-5), env must exist
#   ./build_env.sh --dry-run            # print every command, touch nothing
#   ./build_env.sh --lerobot-repo ~/lerobot     # defaults to this repo's parent directory
#
# The default name is "lerobot" because that is what every other script's header says to
# activate. If one already exists the script refuses rather than touching it -- so on a machine
# that already has a working env, this is safe to run by accident.
#
# WHY THIS SCRIPT EXISTS: environment.yml is a plain `conda env export` snapshot, and
# `conda env create -f environment.yml` cannot reproduce it. Three separate reasons:
#
#   1. The ROCm wheels are not on PyPI. torch==2.12.0+rocm7.14.0, torchvision, triton and the
#      amd-torch-device-* / rocm-sdk-* packages come from AMD's own index (see $ROCM_INDEX
#      below). `conda env export` records the versions but throws the index URL away, so pip
#      goes to PyPI, finds no such version, and the whole create aborts.
#
#   2. `lerobot==0.6.1` is really an editable install of the checkout next door -- the version
#      on PyPI does not carry lerobot_patches/. Installing what the snapshot literally says
#      gives a lerobot that does not know what an XLeRobot is.
#
#   3. opencv has to be the GUI build and it gets clobbered. lerobot pins
#      opencv-python-headless, so `pip install -e .` pulls it in and it overwrites the GUI cv2
#      (they share one directory). It therefore has to be repaired last, after everything else.
#
# So the order below is deliberate: conda packages, then ROCm torch from AMD, then lerobot
# editable, then the remaining pins (which re-assert the snapshot over whatever lerobot's own
# dependency ranges dragged in), then opencv, then a verification pass.
#
# If a step fails, fix the cause and continue with --resume-from N rather than starting over --
# step 1 and step 2 are the slow ones. The exception is a failure that already polluted the env
# with packages later steps will not remove (step 3 resolving torch is the known case): there,
# --force is cleaner than trying to unpick it.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

NAME="lerobot"
YML="$HERE/environment.yml"
LEROBOT_REPO="$(cd "$HERE/.." && pwd)"
FORCE=0
DRY=0
RESUME=1

# Provenance: recovered from this machine's pip cache, and verified to serve the exact pinned
# wheels (torch-2.12.0+rocm7.14.0-cp312-cp312-linux_x86_64.whl and friends). The nightly index
# at rocm.nightlies.amd.com only keeps recent builds and no longer has this one.
ROCM_INDEX="https://repo.amd.com/rocm/whl-multi-arch/"

while [ $# -gt 0 ]; do
  case "$1" in
    --name)          NAME="$2"; shift 2 ;;
    --file)          YML="$2"; shift 2 ;;
    --lerobot-repo)  LEROBOT_REPO="$2"; shift 2 ;;
    --rocm-index)    ROCM_INDEX="$2"; shift 2 ;;
    --force)         FORCE=1; shift ;;
    --resume-from)   RESUME="$2"; shift 2 ;;
    --dry-run)       DRY=1; shift ;;
    -h|--help)       sed -n '2,34p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
    *)               echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

run() {
  printf '\n  $ %s\n' "$*"
  [ "$DRY" = 1 ] && return 0
  "$@"
}

die() { echo "error: $*" >&2; exit 1; }

# --- preflight ---------------------------------------------------------------------------------
command -v conda >/dev/null || die "conda not on PATH"

# A stale `export LD_PRELOAD=/opt/rocm-*/lib/libhsa-runtime64.so.1` -- the workaround the older
# torch 2.10.0+rocm7.0 needed -- makes `import torch` die with
#   libamdhip64.so.7: undefined symbol: hsa_amd_vmem_export_fabric_handle, version ROCR_1
# which reads like a broken install and is not one. /opt/rocm-7.2.0's libhsa does not export that
# symbol, and preloading it beats the RPATH that would otherwise have found the bundled copy in
# site-packages/_rocm_sdk_core/lib. Unset for this run; the closing message says to unset it for
# good.
STALE_PRELOAD=""
if [ -n "${LD_PRELOAD:-}" ]; then
  STALE_PRELOAD="$LD_PRELOAD"
  unset LD_PRELOAD
fi
[ -f "$YML" ]                || die "no such file: $YML"
[ -f "$LEROBOT_REPO/pyproject.toml" ] \
  || die "$LEROBOT_REPO does not look like the lerobot checkout (no pyproject.toml); pass --lerobot-repo"
case "$RESUME" in 1|2|3|4|5) ;; *) die "--resume-from takes a step number 1-5, got '$RESUME'" ;; esac

# mamba solves this 141-package pinned list in seconds where conda takes minutes.
SOLVER=conda
command -v mamba >/dev/null && SOLVER=mamba

if conda env list | awk '{print $1}' | grep -qx "$NAME"; then
  if [ "$RESUME" -gt 1 ]; then
    :   # continuing into it on purpose
  elif [ "$FORCE" = 1 ]; then
    run conda env remove -n "$NAME" -y
  else
    die "env '$NAME' already exists -- --force to replace it, --resume-from N to continue a failed run, or --name something-else"
  fi
elif [ "$RESUME" -gt 1 ]; then
  die "--resume-from $RESUME needs env '$NAME' to exist already"
fi

# true when step N should run
step() { [ "$1" -ge "$RESUME" ]; }
skipped() { echo; echo "=== $1/5  skipped (--resume-from $RESUME) ==="; }

echo "rebuilding '$NAME' from $YML"
echo "  solver        $SOLVER"
echo "  lerobot repo  $LEROBOT_REPO"
echo "  rocm index    $ROCM_INDEX"
[ "$DRY" = 1 ] && echo "  (dry run -- nothing will be changed)"

# --- split the snapshot ------------------------------------------------------------------------
# No PyYAML available yet (the env being built is where it lives), so this is done with awk
# against the very regular layout `conda env export` emits.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

awk -v name="$NAME" -v conda="$TMP/conda.yml" -v pip="$TMP/pip.txt" '
  /^prefix:/            { inpip=0; next }              # dead path from the machine it was exported on
  /^name:/              { print "name: " name > conda; next }
  /^  - pip:/           { inpip=1; next }
  inpip && /^ +- /      { sub(/^ +- /, ""); print > pip; next }
  inpip                 { inpip=0 }                    # anything else closes the pip block
                        { print > conda }
' "$YML"

[ -s "$TMP/pip.txt" ] || die "no pip section found in $YML -- is it really a conda env export?"

pin() { grep -m1 -E "^$1==" "$TMP/pip.txt" || die "$1 not pinned in $YML"; }
OPENCV_PIN="$(pin opencv-python)"

# What step 2 installs from AMD's index rather than PyPI:
#   torch / torchvision / triton   -- the +rocm local versions
#   *-device-gfx*                  -- the per-GPU packages
# The device packages have to be named EXPLICITLY. torch depends on `rocm[libraries]`, which
# brings in rocm-sdk-core and rocm-sdk-libraries but deliberately not any device package -- the
# GPU architecture is the user's choice, so nothing can pull it in for you. Omit them and the
# install looks completely successful right up to the point where torch cannot talk to the GPU.
# Taking them from the snapshot rather than hardcoding gfx1151 means this follows the export.
grep -E '^(torch|torchvision|triton)==|^(amd-torch-device|amd-torchvision-device|rocm-sdk-device)' \
  "$TMP/pip.txt" > "$TMP/rocm.txt"
[ -s "$TMP/rocm.txt" ] || die "no torch/rocm pins found in $YML"
grep -q -- '-device-gfx' "$TMP/rocm.txt" \
  || die "$YML pins no *-device-gfx* package; the env it came from could not have used the GPU"

# Everything the later bulk step must NOT touch:
#   torch/torchvision/triton + amd-*/rocm* -> installed from the AMD index in step 2
#   lerobot                                -> installed editable in step 3
#   opencv                                 -> repaired last in step 5
#   triton-rocm                            -> dropped entirely, see below
# torchcodec does stay in the bulk list: it really is a PyPI package. It will not import
# afterwards, and that is expected rather than a failure -- see SETUP.md.
#
# WHY triton-rocm IS DROPPED: it is a fossil of the older torch 2.10.0+rocm7.0 install from
# download.pytorch.org/whl/rocm7.0, and `conda env export` cannot tell a fossil from a live
# dependency. PyPI only carries triton-rocm 3.0.0rc1, so the bulk step dies on the 3.6.0 pin.
# Installing it would be worse than skipping it: triton-rocm and triton both own
# site-packages/triton/, so it would overwrite the working 3.7.1+rocm7.14.0 from the AMD index
# with 3.6.0. On the machine this snapshot came from that overwrite already happened in reverse
# -- the triton_rocm-3.6.0.dist-info survives but `import triton` reports 3.7.1.
#
# Every other pin in the snapshot was checked against PyPI at its exact version; this is the
# only one that is not there.
grep -vE '^(torch|torchvision|triton|triton-rocm|lerobot|opencv-python|opencv-python-headless)==' "$TMP/pip.txt" \
  | grep -vE '^(amd-torch|amd-torchvision|rocm)' > "$TMP/rest.txt"

echo
echo "  conda packages   $(grep -c '^  - ' "$TMP/conda.yml")"
echo "  pip packages     $(wc -l < "$TMP/pip.txt") total, $(wc -l < "$TMP/rest.txt") in the bulk step"

# --- 1. conda packages -------------------------------------------------------------------------
if step 1; then
  echo; echo "=== 1/5  conda packages (python, ffmpeg, the X/GL stack) ==="
  run $SOLVER env create -f "$TMP/conda.yml"
else
  skipped 1
fi

if [ "$DRY" = 1 ]; then
  ENV_PY="<env>/bin/python"
else
  ENV_PY="$(conda run -n "$NAME" python -c 'import sys; print(sys.executable)')"
fi
PIP=("$ENV_PY" -m pip install --no-input)

# --- 2. ROCm torch from AMD's index ------------------------------------------------------------
# --index-url (not --extra-index-url) so the +rocm local versions resolve; PyPI is added back as
# a fallback for the pure-python dependencies these wheels pull in. This drags in rocm==7.14.0,
# rocm-sdk-core, rocm-sdk-device-gfx1151 and amd-torch-device-gfx1151 as dependencies -- that is
# where the actual ROCm runtime lives in this setup, not in /opt/rocm.
if step 2; then
  echo; echo "=== 2/5  ROCm torch stack from $ROCM_INDEX ==="
  sed 's/^/    /' "$TMP/rocm.txt"
  run "${PIP[@]}" --index-url "$ROCM_INDEX" --extra-index-url https://pypi.org/simple \
    -r "$TMP/rocm.txt"
else
  skipped 2
fi

# --- 3. lerobot, editable ----------------------------------------------------------------------
# --no-deps here is ESSENTIAL, not an optimisation. lerobot's pyproject pins
# `torch>=2.7,<2.12.0` and `torchvision>=0.22.0,<0.27.0`; the snapshot has 2.12.0+rocm7.14.0 and
# 0.27.0+rocm7.14.0 -- both land just outside those ranges. Let pip resolve and it dutifully
# throws away everything step 2 just installed and pulls torch 2.11.0+cu130 from PyPI instead:
# an upstream CUDA wheel, on a machine whose GPU is AMD, dragging ~19 nvidia-* packages and
# several GB along with it.
#
# The snapshot is knowingly inconsistent with those ranges -- `pip check` against the working
# env reports exactly those two lines -- so the only faithful way to install it is to not
# resolve at all. Nothing is lost: rest.txt in step 4 is the full pinned closure.
if step 3; then
  echo; echo "=== 3/5  lerobot (editable, $LEROBOT_REPO) ==="
  run "${PIP[@]}" --no-deps -e "$LEROBOT_REPO"
else
  skipped 3
fi

# --- 4. the rest of the snapshot ---------------------------------------------------------------
# --no-deps for the same reason as step 3, and it costs nothing: rest.txt is the complete pinned
# closure of the snapshot (a `conda env export` lists everything installed, at an exact version),
# so there is nothing for a resolver to add and everything for it to break.
if step 4; then
echo; echo "=== 4/5  remaining pinned pip packages ==="
if ! run "${PIP[@]}" --no-deps -r "$TMP/rest.txt"; then
  cat >&2 <<'HINT'

Step 4 failed. If the error was "Could not find a version that satisfies the requirement X",
then X is pinned in environment.yml but is not on PyPI -- almost always a fossil of an earlier
install from some other index, which `conda env export` records indistinguishably from a live
dependency (triton-rocm was exactly this). Work out whether it is still needed: if not, add it
to the exclusion filter near the top of this script; if it is, install it separately from its
own index, the way step 2 does.
HINT
  exit 1
fi
else
  skipped 4
fi

# --- 5. opencv ---------------------------------------------------------------------------------
# Both packages have to go first: they unpack into the same cv2/ directory, so whichever is
# installed second silently overwrites the other. See SETUP.md, "opencv 必須換成 GUI 版".
echo; echo "=== 5/5  opencv -> GUI build ($OPENCV_PIN) ==="
run "$ENV_PY" -m pip uninstall -y opencv-python opencv-python-headless
run "${PIP[@]}" --no-deps "$OPENCV_PIN"

# --- verify ------------------------------------------------------------------------------------
if [ "$DRY" = 1 ]; then
  echo; echo "dry run complete."
  exit 0
fi

echo; echo "=== verification ==="
"$ENV_PY" - <<'PY'
import importlib, sys

def check(label, fn):
    try:
        print(f"  {label:14} {fn()}")
    except Exception as e:
        print(f"  {label:14} FAILED: {type(e).__name__}: {e}")

check("torch",   lambda: f"{importlib.import_module('torch').__version__}")
check("gpu",     lambda: (lambda t: f"{t.cuda.is_available()}  {t.cuda.get_device_name(0) if t.cuda.is_available() else ''}")(importlib.import_module("torch")))
check("triton",  lambda: importlib.import_module("triton").__version__)
check("cv2 GUI", lambda: "Qt:" in importlib.import_module("cv2").getBuildInformation())
check("lerobot", lambda: importlib.import_module("lerobot").__file__)

# Expected to fail -- torchcodec is built against upstream torch and its ABI does not match the
# rocm-sdk build. That is why every dataset here is recorded with --images-not-video. A *success*
# here is the surprise, and would mean video datasets are usable again.
try:
    importlib.import_module("torchcodec")
    print("  torchcodec     imports -- unexpected, video datasets may now work (see SETUP.md)")
except Exception as e:
    print(f"  torchcodec     does not import, as expected ({type(e).__name__}) -> keep --images-not-video")
PY

# The snapshot does not satisfy lerobot's own declared ranges, on purpose (see step 3). These
# three lines are what a correct build looks like; anything else here is a real problem.
echo
echo "  pip check -- these three lines are expected, anything else is not:"
"$ENV_PY" -m pip check 2>&1 | sed 's/^/    /' || true

echo
echo "done.  conda activate $NAME"

if [ -n "$STALE_PRELOAD" ]; then
  cat <<EOF

WARNING: your shell has LD_PRELOAD=$STALE_PRELOAD
This run ignored it, but your shell has not. With it set, \`import torch\` fails with
"libamdhip64.so.7: undefined symbol: hsa_amd_vmem_export_fabric_handle" -- which looks like a
broken environment but is just the preload. It is the workaround the older torch 2.10.0+rocm7.0
needed and this one must not have. Drop it:

    unset LD_PRELOAD

and remove the export from wherever you set it. train_pipeline.sh and eval.sh only need
TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1.
EOF
fi
