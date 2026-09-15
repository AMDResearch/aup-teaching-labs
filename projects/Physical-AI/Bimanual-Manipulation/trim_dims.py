#!/usr/bin/env python
"""Write a copy of a dataset keeping only the arm channels of `observation.state` and `action`.

Why this exists: the base-velocity channels were never commanded, so their action values are
*exactly* zero in every frame and their standard deviation is exactly 0. ACT normalises actions
with MEAN_STD, so the target becomes (0-0)/0 = NaN and the loss is NaN from the first step -- three
20000-step runs produced nothing but NaN weights. The head channels are near-constant for the same
reason (they were never driven) and waste capacity even where they do not break anything.

Keeping only the 12 arm joints removes both problems. Images, timestamps and task indices are
copied through untouched.

Per-dimension statistics need no recomputation: mean, std, min, max and the quantiles are all
per-channel, so the statistics of a subset of channels are exactly the corresponding entries.

`--keep` narrows it further, to one arm. The phase datasets from record_phases.sh hold both arms
in every phase, because the demonstration is one continuous motion and there is no point recording
it twice -- but the phases are meant for separate per-arm policies, and the arm that is idle during
a phase contributes twelve channels of near-constant state plus a moving distractor in the images.
Splitting at training time keeps the recording decision and the policy decision independent.

    python trim_dims.py blue                          # 17 -> 12, both arms
    python trim_dims.py yellow blue green all
    python trim_dims.py blue --suffix _12d

    # per-arm, from the phase datasets:
    python trim_dims.py open_blue --keep right --prefix xlerobot_ --suffix _right
    python trim_dims.py pick_blue --keep left  --prefix xlerobot_ --suffix _left
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

import pandas as pd

DATA = Path.home() / "lerobot_data"
# What to keep. Everything else in the 17-wide vector is head (constant) or base velocity (exactly
# zero), in that order: 12 arm joints, head_motor_1/2, x.vel, y.vel, theta.vel.
KEEP_SETS = {
    "both": ("left_arm", "right_arm"),
    "left": ("left_arm",),
    "right": ("right_arm",),
}
VECTOR_FEATURES = ("observation.state", "action")


def trim_one(src: Path, dst: Path, keep_prefixes: tuple[str, ...]) -> None:
    info = json.loads((src / "meta" / "info.json").read_text())
    names = info["features"]["observation.state"]["names"]
    keep = [i for i, n in enumerate(names) if n.startswith(keep_prefixes)]
    kept_names = [names[i] for i in keep]
    dropped = [n for i, n in enumerate(names) if i not in keep]

    print(f"  {len(names)} -> {len(keep)} channels")
    print(f"    keeping : {', '.join(kept_names)}")
    print(f"    dropping: {', '.join(dropped)}")
    if not keep:
        sys.exit(f"nothing matched {keep_prefixes} in {names}")

    if dst.exists():
        print(f"    replacing existing {dst.name}")
        shutil.rmtree(dst)
    # Copy first, then rewrite the parts that change. Images are the bulk and are untouched.
    shutil.copytree(src, dst)

    for pq in sorted((dst / "data").rglob("*.parquet")):
        df = pd.read_parquet(pq)
        for col in VECTOR_FEATURES:
            df[col] = df[col].map(lambda v: [v[i] for i in keep])
        df.to_parquet(pq, index=False)
    print(f"    rewrote {len(list((dst / 'data').rglob('*.parquet')))} data file(s)")

    # Per-episode stats live as columns named stats/<feature>/<statistic>.
    for pq in sorted((dst / "meta" / "episodes").rglob("*.parquet")):
        df = pd.read_parquet(pq)
        touched = 0
        for col in df.columns:
            parts = col.split("/")
            if len(parts) == 3 and parts[0] == "stats" and parts[1] in VECTOR_FEATURES:
                # `count` is a single total, not one value per channel.
                if parts[2] == "count":
                    continue
                df[col] = df[col].map(lambda v: [v[i] for i in keep])
                touched += 1
        df.to_parquet(pq, index=False)
        print(f"    rewrote {touched} stats column(s) in {pq.name}")

    stats_path = dst / "meta" / "stats.json"
    stats = json.loads(stats_path.read_text())
    for feat in VECTOR_FEATURES:
        for stat, value in stats[feat].items():
            if stat == "count":
                continue
            stats[feat][stat] = [value[i] for i in keep]
    stats_path.write_text(json.dumps(stats, indent=4))

    for feat in VECTOR_FEATURES:
        info["features"][feat]["shape"] = [len(keep)]
        info["features"][feat]["names"] = kept_names
    (dst / "meta" / "info.json").write_text(json.dumps(info, indent=4))
    print(f"    -> {dst}")


def main() -> None:
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    ap.add_argument("variants", nargs="+",
                    help="dataset names after --prefix, e.g. 'blue' or 'open_blue'")
    ap.add_argument("--keep", default="both", choices=sorted(KEEP_SETS),
                    help="which arm's channels to keep (default both)")
    ap.add_argument("--prefix", default="xlerobot_drawer_",
                    help="dataset name prefix (use xlerobot_ for the phase datasets)")
    ap.add_argument("--suffix", default=None,
                    help="output name suffix (default _arms, or _left/_right with --keep)")
    args = ap.parse_args()

    keep_prefixes = KEEP_SETS[args.keep]
    suffix = args.suffix
    if suffix is None:
        suffix = "_arms" if args.keep == "both" else f"_{args.keep}"

    outputs = []
    for v in args.variants:
        src = DATA / f"{args.prefix}{v}"
        if not (src / "meta" / "info.json").exists():
            sys.exit(f"no dataset at {src}")
        dst = DATA / f"{args.prefix}{v}{suffix}"
        print(f"=== {v} ({args.keep}) ===")
        trim_one(src, dst, keep_prefixes)
        outputs.append(dst)

    print("\nverify with:")
    for dst in outputs:
        print(f"  python -c \"from lerobot.datasets.lerobot_dataset import LeRobotDataset as D;"
              f" d=D('local/{dst.name}', root='{dst}');"
              f" print(d.meta.features['action']['shape'], d[0]['action'].shape)\"")


if __name__ == "__main__":
    main()
