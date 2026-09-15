#!/usr/bin/env python
"""Neutralise the normalisation statistics of channels that never move.

    python fix_constant_stats.py xlerobot_open_blue_arms xlerobot_close_blue_arms
    python fix_constant_stats.py --all-arms          # every *_arms dataset
    python fix_constant_stats.py xlerobot_open_blue_arms --dry-run

THE PROBLEM. These datasets record both arms even though only one is teleoperated; the idle arm's
action is held at a single constant. A constant channel has std 0, and MEAN_STD normalisation
computes (x - mean) / (std + 1e-8). The value and the mean are the same number, but the mean is
stored as a rounded float32, so the numerator is a float rounding residue of order 1e-7 rather
than exactly zero -- and dividing that by 1e-8 yields a target of order 10 to 100:

    close_blue  left_arm_shoulder_pan   std 0.000000  ->  |z| = 119.2
    open_blue   left_arm_shoulder_lift  std 0.000021  ->  |z| =  11.1

Nothing diverges -- the target is a constant, so the model fits it as a bias -- but the reported
loss is dominated by it. Two models over the same task showed initial losses of 0.68 and 10.2
purely from which way the last float bit of the mean happened to round. That makes losses
incomparable between runs, and spends gradient on a meaningless constant.

THE FIX. Set std = 1.0 on the affected channels, leaving the mean as it is. Then z = (x - mean)/1
is the same tiny residue instead of a large number, the loss is dominated by the joints that
actually move, and un-normalising at inference gives z*1 + mean -- which is still the correct
constant, because the residue it adds back is the one it started from. Correct output, honest loss.

The alternative is dropping the idle arm's channels outright (trim_dims.py --keep right|left, 6
channels). That is the cleaner answer, but it needs the robot to advertise 6 action features at
rollout time and XLerobotConfig only offers 12 (arms_only_features) or 17. This works with the
plumbing that already exists and is verified.

Only `meta/stats.json` is touched -- the per-episode statistics under meta/episodes/ are left
alone, since normalisation is built from the aggregate.
"""

import argparse
import glob
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

DATA = Path.home() / "lerobot_data"
VECTOR_FEATURES = ("observation.state", "action")
# The test is on the channel's actual span, not on its std. std alone is not enough: a joint that
# drifted 0.18 units across an episode -- encoder quantisation and thermal creep, no motion at all
# -- had std 0.0127, which passed a std-based floor of 0.01 and then normalised that drift to
# |z| = 10.8. Span separates the two cleanly, because there is an order of magnitude of empty room
# between them: the least active joint that really moves here spans about 5 units (wrist_roll
# during open/close), and every idle channel spans under 0.3.
SPAN_FLOOR = 1.0


def spans(root: Path, feature: str) -> np.ndarray:
    """Per-channel max - min over the recorded values."""
    tabs = [pq.read_table(f, columns=[feature])
            for f in sorted(glob.glob(str(root / "data" / "**" / "*.parquet"), recursive=True))]
    v = np.array(pa.concat_tables(tabs).column(feature).to_pylist(), dtype=np.float64)
    return v.max(0) - v.min(0)


def fix_one(root: Path, dry_run: bool) -> int:
    info = json.loads((root / "meta" / "info.json").read_text())
    stats_path = root / "meta" / "stats.json"
    stats = json.loads(stats_path.read_text())
    touched = 0

    for feat in VECTOR_FEATURES:
        if feat not in stats:
            continue
        names = info["features"][feat]["names"]
        std = stats[feat]["std"]
        span = spans(root, feat)
        for i, name in enumerate(names):
            if span[i] >= SPAN_FLOOR or std[i] >= SPAN_FLOOR:
                continue
            print(f"    {feat:18s} {name:30s} span {span[i]:9.4f}  std {std[i]:11.8f} -> 1.0"
                  f"   (|z| was up to {span[i] / 2 / (std[i] + 1e-8):7.1f})")
            if not dry_run:
                std[i] = 1.0
            touched += 1

    if touched and not dry_run:
        shutil.copy(stats_path, stats_path.with_suffix(".json.orig"))
        stats_path.write_text(json.dumps(stats, indent=4))
        print(f"    rewrote {stats_path}  (original kept as stats.json.orig)")
    elif not touched:
        print("    nothing to fix")
    return touched


def main() -> None:
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    ap.add_argument("datasets", nargs="*", help="dataset directory names under ~/lerobot_data")
    ap.add_argument("--all-arms", action="store_true",
                    help="every directory matching *_arms")
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    args = ap.parse_args()

    targets = list(args.datasets)
    if args.all_arms:
        targets += sorted(p.name for p in DATA.glob("*_arms") if (p / "meta").is_dir())
    if not targets:
        sys.exit("nothing to do -- name some datasets or pass --all-arms")

    total = 0
    for name in dict.fromkeys(targets):
        root = DATA / name
        if not (root / "meta" / "stats.json").exists():
            print(f"=== {name}: no stats.json, skipping ===")
            continue
        print(f"=== {name} ===")
        total += fix_one(root, args.dry_run)

    print(f"\n{'would fix' if args.dry_run else 'fixed'} {total} channel(s)")
    if total and not args.dry_run:
        print("retrain anything already trained on these -- its loss was dominated by the "
              "constant channels and is not comparable to a run after this fix")


if __name__ == "__main__":
    main()
