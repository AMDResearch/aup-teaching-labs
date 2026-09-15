#!/usr/bin/env bash
# Prepare every phase dataset, verify it, then train the ten policies the drawer pipeline needs.
# Designed to be started and left alone.
#
#   conda activate lerobot
#   ./train_pipeline.sh              # prepare, verify, train everything
#   ./train_pipeline.sh --prep-only  # stop after preparing and verifying
#   ./train_pipeline.sh --train-only # skip preparation, verify then train
#   ./train_pipeline.sh --status     # what is done so far
#
#   nine ACT models    open/close/pick x yellow/blue/green   5000 steps, checkpoint every 1000
#   one SmolVLA model  pick, all three colours merged        20000 steps, checkpoint every 5000
#
# Roughly 5.5h for the ACT models plus 7h for SmolVLA. They run one at a time so they do not
# contend for the GPU; a failure in one is reported and the rest continue.
#
# PREPARATION MATTERS MORE THAN IT LOOKS. Each recording holds both arms even though only one was
# teleoperated, so the idle arm's channels are exactly constant -- and a constant channel makes
# MEAN_STD normalisation divide a float32 rounding residue by 1e-8, producing a target of order 10
# to 100. Training on that looks completely normal (loss fell to 0.131, as good as a run that
# worked) and yields a policy that ignores its inputs: on the robot it commanded 0.2 units of
# travel where the demonstrations cover 344. So the pipeline trims, merges, repairs the statistics,
# and then refuses to train at all unless verify_arms.py passes.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="$HOME/lerobot_data"
OUT_BASE="$HOME/lerobot_out"
# Many images ship python3 without a bare `python` -- the training container is one.
PY="${PY:-$(command -v python || command -v python3)}"
COLOURS=(yellow blue green)

ACT_STEPS="${ACT_STEPS:-5000}"
ACT_SAVE="${ACT_SAVE:-1000}"
ACT_BATCH="${ACT_BATCH:-8}"
VLA_STEPS="${VLA_STEPS:-20000}"
VLA_SAVE="${VLA_SAVE:-5000}"
VLA_BATCH="${VLA_BATCH:-16}"
WORKERS="${WORKERS:-8}"

# smolvla_base's own config names its cameras camera1/2/3. The finetune keeps those names, and
# build_rollout_context later compares them against what the robot provides -- so the same map has
# to be given at eval time. camera3 goes unfilled and SmolVLA pads it, exactly as in training.
RENAME_MAP='{"observation.images.head": "observation.images.camera1",
             "observation.images.wrist": "observation.images.camera2"}'

# See SETUP.md: this torch (2.12.0+rocm7.14.0) must NOT have the system HSA runtime preloaded.
export TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1

# --- job table: name, dataset, kind ------------------------------------------------------------
JOBS=()
for phase in open close; do
  for c in "${COLOURS[@]}"; do JOBS+=("act:act_${phase}_${c}:xlerobot_${phase}_${c}_arms"); done
done
# One ACT per colour for pick as well, so the pick phase can be run either way. It is the
# interesting comparison of the whole project: three specialists each shown 18 demonstrations of a
# single drawer, against one language-conditioned model shown all 54 and told which drawer in
# words. The specialists cannot generalise but have an easier problem; SmolVLA has to read the
# instruction but sees three times the data and shares the reach-and-grasp motion across colours.
for c in "${COLOURS[@]}"; do JOBS+=("act:act_pick_${c}:xlerobot_pick_${c}_arms"); done
JOBS+=("smolvla:smolvla_pick_all:xlerobot_pick_all_arms")

status() {
  printf '%-22s %-8s %-9s %s\n' model kind steps checkpoints
  for job in "${JOBS[@]}"; do
    IFS=: read -r kind name _ <<<"$job"
    d="$OUT_BASE/$name"
    want=$([ "$kind" = act ] && echo "$ACT_STEPS" || echo "$VLA_STEPS")
    if [ -d "$d/checkpoints" ]; then
      cks=$(ls "$d/checkpoints" | grep -E '^0' | tr '\n' ' ')
      last=$(basename "$(readlink -f "$d/checkpoints/last" 2>/dev/null)" 2>/dev/null)
      printf '%-22s %-8s %-9s %s\n' "$name" "$kind" "$((10#${last:-0}))/$want" "$cks"
    else
      printf '%-22s %-8s %-9s %s\n' "$name" "$kind" "0/$want" "-"
    fi
  done
}

[ "${1:-}" = "--status" ] && { status; exit 0; }

# Environment gate, before anything reads a dataset. Without it the first sign of a broken GPU
# setup is a bare `assert torch.cuda.is_available()` inside get_safe_torch_device -- an
# AssertionError with no message, raised after the whole preparation stage has already run.
"$PY" "$HERE/verify_env.py" --train || { echo; echo "ABORTED: fix the environment first." >&2; exit 1; }

# --train-only still verifies: preparation is skippable, but starting a twelve-hour run on
# unchecked normalisation is what this script exists to prevent.
if [ "${1:-}" = "--train-only" ]; then
  echo "=== verifying (preparation skipped) ==="
  "$PY" "$HERE/training/verify_arms.py" || { echo; echo "ABORTED: fix the above first." >&2; exit 1; }
  SKIP_PREP=1
else
  SKIP_PREP=0
fi

if [ "$SKIP_PREP" = 0 ]; then
# --- 1. trim every recording to the 12 arm channels -------------------------------------------
echo "=== 1/4  trimming recordings to 12 channels ==="
TRIM=()
for phase in open pick close; do
  for c in "${COLOURS[@]}"; do
    [ -f "$DATA/xlerobot_${phase}_${c}/meta/info.json" ] && TRIM+=("${phase}_${c}")
  done
done
[ ${#TRIM[@]} -gt 0 ] || { echo "ERROR: no phase recordings found under $DATA" >&2; exit 1; }
"$PY" "$HERE/training/trim_dims.py" "${TRIM[@]}" --keep both --prefix xlerobot_ \
  | grep -E '^===|channels|-> ' || exit 1

# --- 2. merge the three pick sets, so SmolVLA sees all three instructions ----------------------
# lerobot-train takes one dataset: make_dataset() raises NotImplementedError the moment repo_id is
# a list. The merge remaps task indices rather than flattening them, so all three task strings
# survive -- which is the whole point, they are what names the target drawer.
echo
echo "=== 2/4  merging the three pick sets ==="
PICK_ALL="$DATA/xlerobot_pick_all_arms"
repo_ids=(); roots=()
for c in "${COLOURS[@]}"; do
  repo_ids+=("local/xlerobot_pick_${c}_arms"); roots+=("$DATA/xlerobot_pick_${c}_arms")
done
join() { local IFS=,; echo "[$*]"; }
rm -rf "$PICK_ALL"
lerobot-edit-dataset \
  --operation.type=merge \
  --operation.repo_ids="$(join "${repo_ids[@]}")" \
  --operation.roots="$(join "${roots[@]}")" \
  --new_repo_id=local/xlerobot_pick_all_arms \
  --new_root="$PICK_ALL" || { echo "ERROR: merge failed" >&2; exit 1; }

# --- 3. repair the statistics of every constant channel ----------------------------------------
# After the merge, not before: the merge rebuilds stats.json from the source data, so a repair done
# earlier would be undone. The three parking poses differ by 0.09 units, which is enough to give the
# merged channel a small non-zero std and hide the problem while still poisoning the targets.
echo
echo "=== 3/4  repairing normalisation statistics ==="
"$PY" "$HERE/training/fix_constant_stats.py" --all-arms || exit 1

# --- 4. verify before spending a night on it ----------------------------------------------------
echo
echo "=== 4/4  verifying ==="
if ! "$PY" "$HERE/training/verify_arms.py"; then
  echo
  echo "ABORTED before training: fix the above first." >&2
  exit 1
fi

fi   # SKIP_PREP

[ "${1:-}" = "--prep-only" ] && { echo; echo "prep only, stopping here."; exit 0; }

# --- train -------------------------------------------------------------------------------------
train_act() {
  local name="$1" root="$2" out="$OUT_BASE/$1" log="$OUT_BASE/run_$1.log"
  lerobot-train \
    --dataset.repo_id="local/$(basename "$root")" \
    --dataset.root="$root" \
    --policy.type=act \
    --policy.use_vae=false \
    --policy.device=cuda \
    --policy.push_to_hub=false \
    --output_dir="$out" \
    --job_name="$name" \
    --batch_size="$ACT_BATCH" \
    --num_workers="$WORKERS" \
    --steps="$ACT_STEPS" \
    --save_freq="$ACT_SAVE" \
    --log_freq=100 \
    --wandb.enable=false > "$log" 2>&1
}

train_smolvla() {
  local name="$1" root="$2" out="$OUT_BASE/$1" log="$OUT_BASE/run_$1.log"
  lerobot-train \
    --policy.path=lerobot/smolvla_base \
    --dataset.repo_id="local/$(basename "$root")" \
    --dataset.root="$root" \
    --rename_map="$RENAME_MAP" \
    --output_dir="$out" \
    --job_name="$name" \
    --batch_size="$VLA_BATCH" \
    --num_workers="$WORKERS" \
    --steps="$VLA_STEPS" \
    --save_freq="$VLA_SAVE" \
    --log_freq=100 \
    --policy.optimizer_lr=5e-5 \
    --policy.use_amp=true \
    --policy.train_expert_only=true \
    --policy.freeze_vision_encoder=true \
    --policy.device=cuda \
    --policy.push_to_hub=false \
    --seed=1000 \
    --wandb.enable=false > "$log" 2>&1
}

echo
echo "=== training ${#JOBS[@]} models, one at a time ==="
echo "    ACT     ${ACT_STEPS} steps, checkpoint every ${ACT_SAVE}, batch ${ACT_BATCH}"
echo "    SmolVLA ${VLA_STEPS} steps, checkpoint every ${VLA_SAVE}, batch ${VLA_BATCH}"
echo "    started $(date '+%F %H:%M')"
FAILED=()
for job in "${JOBS[@]}"; do
  IFS=: read -r kind name root <<<"$job"
  out="$OUT_BASE/$name"
  # A finished run is left alone, so the script can be re-run after an interruption without
  # throwing away hours of work.
  if [ -d "$out/checkpoints/last" ]; then
    done_at=$(basename "$(readlink -f "$out/checkpoints/last")")
    want=$([ "$kind" = act ] && echo "$ACT_STEPS" || echo "$VLA_STEPS")
    if [ "$((10#$done_at))" -ge "$want" ]; then
      echo "--- $name already at $((10#$done_at)) steps, skipping"
      continue
    fi
    echo "--- $name has $((10#$done_at)) steps but wants $want; starting fresh"
    mv "$out" "$out.partial.$(date +%s)"
  fi
  echo "--- $name  ($kind on $root)  $(date '+%H:%M')"
  if [ "$kind" = act ]; then train_act "$name" "$DATA/$root"; else train_smolvla "$name" "$DATA/$root"; fi
  if [ $? -ne 0 ]; then
    echo "    FAILED -- see $OUT_BASE/run_$name.log"
    FAILED+=("$name")
  else
    loss=$(tr '\r' '\n' < "$OUT_BASE/run_$name.log" | grep -oE 'loss:[0-9.]+' | tail -1)
    echo "    done $(date '+%H:%M')  final ${loss:-loss:?}"
  fi
done

echo
echo "=== finished $(date '+%F %H:%M') ==="
status
if [ ${#FAILED[@]} -gt 0 ]; then
  echo
  echo "${#FAILED[@]} run(s) failed: ${FAILED[*]}"
  exit 1
fi
echo
echo "evaluate with:"
echo "  ./eval.sh act/open_blue blue 40 005000"
echo "  python eval/chain_eval.py --stages act_open_blue,act_close_blue --checkpoint 005000"
