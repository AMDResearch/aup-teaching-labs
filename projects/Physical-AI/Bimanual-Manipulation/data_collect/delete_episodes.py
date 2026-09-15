#!/usr/bin/env python
"""Delete episodes from any of the local datasets.

    python delete_episodes.py yellow 3                    # drop episode 3
    python delete_episodes.py blue 3 7                    # drop several
    python delete_episodes.py green 3 --keep-backup

    # the per-phase datasets from record_phases.sh:
    python delete_episodes.py open_yellow 0 --prefix xlerobot_
    python delete_episodes.py close_yellow 0 --prefix xlerobot_

Deleting the same cycle from open and close takes two calls -- they are separate
datasets and nothing links them, so it is on you to pass the same index to both
(the cycle numbering matches as long as every cycle was completed with `c`).

lerobot-edit-dataset can't do this here: get_output_path() treats --root as the
*parent* of <repo_id>, while LeRobotDataset treats it as the dataset directory
itself, so the in-place path looks for the source under <root>_old and dies.
This calls the underlying delete_episodes() with an explicit output_dir and
swaps the result in.

Remaining episodes are renumbered to stay contiguous -- deleting 3 makes the old
4 become the new 3 -- so indices shift after every delete. Re-check before
deleting again, and re-run ./train_pipeline.sh --prep-only afterwards since the
trimmed and merged sets it builds are copies.
"""

import argparse
import shutil
import sys
from pathlib import Path

from lerobot.datasets.dataset_tools import delete_episodes
from lerobot.datasets.lerobot_dataset import LeRobotDataset

def main() -> None:
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    ap.add_argument("dataset", help="dataset name after --prefix, e.g. 'yellow' or 'open_yellow'")
    ap.add_argument("indices", type=int, nargs="+", help="episode indices to delete")
    ap.add_argument("--prefix", default="xlerobot_drawer_",
                    help="dataset name prefix (use xlerobot_ for the per-phase datasets)")
    ap.add_argument("--keep-backup", action="store_true", help="leave the pre-edit copy on disk")
    args = ap.parse_args()

    name = f"{args.prefix}{args.dataset}"
    repo_id = f"local/{name}"
    root = Path.home() / "lerobot_data" / name
    if not (root / "meta" / "tasks.parquet").exists():
        sys.exit(f"no dataset at {root}")

    tmp = root.parent / f"{root.name}_new"
    old = root.parent / f"{root.name}_preedit"
    for p in (tmp, old):
        if p.exists():
            shutil.rmtree(p)

    ds = LeRobotDataset(repo_id, root=root)
    before = ds.meta.total_episodes
    bad = [i for i in args.indices if i >= before]
    if bad:
        sys.exit(f"no such episode: {bad} (dataset has 0..{before - 1})")

    for i in args.indices:
        a = ds.meta.episodes["dataset_from_index"][i]
        b = ds.meta.episodes["dataset_to_index"][i]
        print(f"deleting ep {i}: {b - a} frames ({(b - a) / ds.meta.fps:.1f}s)")

    new_ds = delete_episodes(ds, episode_indices=args.indices, output_dir=tmp, repo_id=repo_id)
    after = new_ds.meta.total_episodes
    print(f"{before} -> {after} episodes")

    if after != before - len(set(args.indices)):
        sys.exit(f"unexpected episode count, leaving new dataset at {tmp}")

    # only now touch the original
    root.rename(old)
    tmp.rename(root)

    if args.keep_backup:
        print(f"pre-edit copy kept at {old}")
    else:
        shutil.rmtree(old)
    print(f"done -> {root}")
    if args.prefix == "xlerobot_drawer_":
        print("remember: ./train_pipeline.sh --prep-only before training, the trimmed sets are copies")


if __name__ == "__main__":
    main()
