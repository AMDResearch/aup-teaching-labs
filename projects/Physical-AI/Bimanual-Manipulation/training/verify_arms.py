#!/usr/bin/env python
"""Verify the trimmed `_arms` datasets are fit to train on. Exits non-zero if any are not.

    python verify_arms.py                       # every *_arms dataset
    python verify_arms.py open_blue_arms ...

This exists because the failure it catches is silent. A channel that never varies gives MEAN_STD
normalisation `(x - mean) / (std + 1e-8)` with a float32 rounding residue on top, producing a
constant target of order 10 to 100. Training looks entirely healthy -- loss falls smoothly to a
normal-looking value -- but the largest gradient signal in the run is that meaningless constant,
and the policy that comes out ignores its inputs. On the robot it commands the mean of the
demonstrated trajectory and holds it: 0.2 units of travel where the demonstrations cover 344.

Nothing in the loss curve distinguishes that from a good run, so it has to be checked here. The
reference is a dataset known to train into a working policy (`xlerobot_drawer_blue_arms`, action
mean|z| 0.81, worst channel 4.5); anything much above that is suspect.
"""

import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

DATA = Path.home() / "lerobot_data"
EPS = 1e-8                 # what lerobot's normaliser adds to std
MEAN_Z_LIMIT = 1.2         # the working reference sits at 0.81
MAX_Z_LIMIT = 8.0          # the working reference peaks at 4.5
VECTORS = ("observation.state", "action")
# What each dataset should hold, so a half-finished trim or a stale merge is caught here rather
# than after hours of training.
EXPECTED_EPISODES = {"open": 10, "close": 10, "pick": 18}


def z_stats(root: Path, feature: str):
    info = json.loads((root / "meta" / "info.json").read_text())
    names = info["features"][feature]["names"]
    st = json.loads((root / "meta" / "stats.json").read_text())[feature]
    mean = np.array(st["mean"], dtype=np.float32)
    std = np.array(st["std"], dtype=np.float32)
    tabs = [pq.read_table(f, columns=[feature])
            for f in sorted(glob.glob(str(root / "data" / "**" / "*.parquet"), recursive=True))]
    v = np.array(pa.concat_tables(tabs).column(feature).to_pylist(), dtype=np.float32)
    z = np.abs((v - mean) / (std + EPS))
    return names, z, info


def check(name: str) -> list[str]:
    root = DATA / name
    problems = []
    if not (root / "meta" / "info.json").exists():
        return [f"{name}: missing"]

    names, z, info = z_stats(root, "action")
    _, zs, _ = z_stats(root, "observation.state")
    dim = info["features"]["action"]["shape"][0]
    eps, frames, fps = info["total_episodes"], info["total_frames"], info["fps"]
    tasks = pq.read_table(root / "meta" / "tasks.parquet").num_rows

    worst = int(z.max(0).argmax())
    print(f"  {name:30s} {dim:2d}ch {eps:3d}eps {frames:6d}fr {fps}fps {tasks} task(s)"
          f"   action mean|z| {z.mean():5.2f}  worst {names[worst].replace('_arm','').replace('.pos','')}"
          f"={z[:, worst].max():.1f}   state mean|z| {zs.mean():5.2f}")

    if dim != 12:
        problems.append(f"{name}: {dim} channels, expected 12 (re-run trim_dims.py --keep both)")
    if fps != 15:
        problems.append(f"{name}: {fps} fps, expected 15")
    if z.mean() > MEAN_Z_LIMIT or z.max() > MAX_Z_LIMIT:
        problems.append(f"{name}: normalisation is pathological (action mean|z| {z.mean():.2f}, "
                        f"max {z.max():.1f}) -- run fix_constant_stats.py")
    if zs.mean() > MEAN_Z_LIMIT or zs.max() > MAX_Z_LIMIT:
        problems.append(f"{name}: state normalisation is pathological "
                        f"(mean|z| {zs.mean():.2f}, max {zs.max():.1f})")

    phase = name.split("_")[1]
    if name.endswith("pick_all_arms") or "_all_" in name:
        if eps != 54:
            problems.append(f"{name}: {eps} episodes, expected 54 (3 x 18) -- re-run the merge")
        if tasks != 3:
            problems.append(f"{name}: {tasks} task strings, expected 3 -- the merge flattened them, "
                            f"and SmolVLA has nothing left to tell the drawers apart")
    elif phase in EXPECTED_EPISODES:
        if eps != EXPECTED_EPISODES[phase]:
            problems.append(f"{name}: {eps} episodes, expected {EXPECTED_EPISODES[phase]}")
        if tasks != 1:
            problems.append(f"{name}: {tasks} task strings, expected 1")

    # A dataset that cannot be opened and indexed is not trainable, whatever the metadata says.
    try:
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        ds = LeRobotDataset(f"local/{name}", root=root)
        s = ds[0]
        if tuple(s["action"].shape) != (12,):
            problems.append(f"{name}: sample action shape {tuple(s['action'].shape)}")
        for cam in ("head", "wrist"):
            key = f"observation.images.{cam}"
            if key not in s:
                problems.append(f"{name}: sample is missing {key}")
    except Exception as e:
        problems.append(f"{name}: will not load -- {type(e).__name__}: {e}")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    ap.add_argument("datasets", nargs="*")
    args = ap.parse_args()
    names = args.datasets or sorted(p.name for p in DATA.glob("xlerobot_*_arms")
                                    if (p / "meta" / "info.json").exists())
    if not names:
        sys.exit("no *_arms datasets found -- run trim_dims.py first")

    print("reference: a dataset that trains into a working policy has action mean|z| ~0.81, "
          "worst channel ~4.5\n")
    problems = []
    for n in names:
        problems += check(n)

    print()
    if problems:
        for p in problems:
            print(f"  ! {p}")
        sys.exit(f"\n{len(problems)} problem(s) -- not safe to train")
    print(f"all {len(names)} dataset(s) look trainable")


if __name__ == "__main__":
    main()
