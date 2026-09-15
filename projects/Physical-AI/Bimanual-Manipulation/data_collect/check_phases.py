#!/usr/bin/env python
"""Sanity-check the per-phase datasets from record_phases.sh before training on them.

    conda activate lerobot
    python check_phases.py                       # every phase/colour that exists
    python check_phases.py open_yellow           # just one
    python check_phases.py --phase open          # all colours of one phase

Beyond the usual counts and shapes, this checks the things specific to how these were recorded:

  * only one arm was teleoperated, so the other arm's action channels must be exactly constant.
    A non-constant idle arm means a leader was attached that should not have been.
  * the driven arm must actually cover ground. A tiny range means the leader lost its grip on the
    follower, or the wrong arm was driven.
  * open and close were cut out of one continuous motion, so the right-arm state at the end of
    open episode k must equal the start of close episode k. A mismatch means a cycle was ended
    early with `r`, which desynchronises the pairing for every later cycle.
  * dead-still stretches at the phase boundary. These are the operator releasing the leader to
    reach for the keyboard. They are not wasted frames so much as contradictory labels: the same
    static observation appears with "keep waiting" and with "start moving", and a chunked policy
    averages the two into a hesitant start. What matters is less the total than the VARIANCE in
    tail length across episodes.
  * images decode, differ from each other, and are not blank.
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
PHASES = ("open", "pick", "close")
COLOURS = ("yellow", "blue", "green")
# The arm each phase is driven by; the other one should be held still.
DRIVEN = {"open": "right_arm", "close": "right_arm", "pick": "left_arm"}
STATIC_EPS = 1e-6      # a per-tick state change below this is quantisation-identical
MIN_RANGE = 10.0       # a driven joint should span at least this much, in [-100,100] units


def read_cols(root: Path, cols: list[str]) -> pa.Table | None:
    tabs = []
    for f in sorted(glob.glob(str(root / "data" / "**" / "*.parquet"), recursive=True)):
        try:
            tabs.append(pq.read_table(f, columns=cols))
        except Exception as e:
            print(f"  ! unreadable {Path(f).name}: {type(e).__name__} "
                  f"(still being written? stop the recorder first)")
    return pa.concat_tables(tabs) if tabs else None


class Phase:
    def __init__(self, name: str):
        self.name = name
        self.root = DATA / f"xlerobot_{name}"
        self.ok = (self.root / "meta" / "info.json").exists()
        if not self.ok:
            return
        self.info = json.loads((self.root / "meta" / "info.json").read_text())
        self.names = self.info["features"]["observation.state"]["names"]
        t = read_cols(self.root, ["observation.state", "action", "timestamp", "episode_index"])
        self.ok = t is not None
        if not self.ok:
            return
        self.ep = np.array(t.column("episode_index").to_pylist())
        self.ts = np.array(t.column("timestamp").to_pylist())
        self.st = np.array(t.column("observation.state").to_pylist(), dtype=np.float32)
        self.ac = np.array(t.column("action").to_pylist(), dtype=np.float32)
        self.eps = sorted(set(self.ep.tolist()))

    def cols(self, prefix: str) -> list[int]:
        return [i for i, n in enumerate(self.names) if n.startswith(prefix)]

    def per_ep(self, arr: np.ndarray) -> list[np.ndarray]:
        return [arr[self.ep == e] for e in self.eps]


def report(p: Phase) -> dict | None:
    print(f"\n--- {p.root.name} ---")
    if not p.ok:
        print("  ! not recorded yet")
        return None

    phase = p.name.split("_")[0]
    driven, idle = DRIVEN[phase], ("left_arm" if DRIVEN[phase] == "right_arm" else "right_arm")
    dcols, icols = p.cols(driven), p.cols(idle)
    problems = []

    lens = [len(s) for s in p.per_ep(p.st)]
    mean = sum(lens) / len(lens)
    print(f"  episodes {len(p.eps)}   frames {len(p.ep)}   fps {p.info['fps']}   "
          f"task {p.info.get('total_tasks')}x")
    print(f"  ep length: min {min(lens)} max {max(lens)} mean {mean:.0f} "
          f"({mean / p.info['fps']:.1f}s)")
    if p.info["total_episodes"] != len(p.eps):
        problems.append(f"meta says {p.info['total_episodes']} episodes, data holds {len(p.eps)}")

    # Timing. dt is the loop period; anything past 1.5x is a stall the policy will read as a jump.
    dts = np.concatenate([np.diff(t) for t in p.per_ep(p.ts) if len(t) > 1])
    period = 1.0 / p.info["fps"]
    gaps = int((dts > 1.5 * period).sum())
    print(f"  timing   : dt mean {dts.mean() * 1000:.1f}ms max {dts.max() * 1000:.1f}ms   "
          f"gaps >1.5x: {gaps}")
    if gaps:
        problems.append(f"{gaps} frame gaps over 1.5x the loop period")

    # Driven vs idle arm.
    arng = p.ac.max(0) - p.ac.min(0)
    idle_moved = [p.names[i] for i in icols if arng[i] > STATIC_EPS]
    weak = [p.names[i] for i in dcols if arng[i] < MIN_RANGE]
    print(f"  driven   : {driven}  range " +
          " ".join(f"{p.names[i].split('_', 2)[2].replace('.pos', '')}={arng[i]:.0f}"
                   for i in dcols))
    print(f"  idle     : {idle}  action constant: {not idle_moved}")
    if idle_moved:
        problems.append(f"idle arm {idle} has non-constant action: {idle_moved}")
    if weak:
        problems.append(f"driven joints barely move (<{MIN_RANGE}): {weak}")

    astd = p.ac[:, dcols].std(0)
    print("  action std: " +
          " ".join(f"{p.names[i].split('_', 2)[2].replace('.pos', '')}={v:.2f}"
                   for i, v in zip(dcols, astd)))
    if (astd < 1e-3).any():
        problems.append("a driven channel has ~zero action std -- MEAN_STD normalisation will "
                        "produce NaN targets; drop it or trim the dataset")

    # Tracking error: how well the follower kept up with the leader.
    err = np.abs(p.ac[:, dcols] - p.st[:, dcols]).mean(0)
    print("  tracking : mean |action-state| " +
          " ".join(f"{p.names[i].split('_', 2)[2].replace('.pos', '')}={v:.1f}"
                   for i, v in zip(dcols, err)))

    # Dead-still stretches at each end of every episode.
    heads, tails, fracs = [], [], []
    for s in p.per_ep(p.st[:, dcols]):
        step = np.abs(np.diff(s, axis=0)).sum(1)
        moving = step >= STATIC_EPS
        if not moving.any():
            heads.append(len(step)); tails.append(len(step)); fracs.append(1.0)
            continue
        heads.append(int(np.argmax(moving)))
        tails.append(int(np.argmax(moving[::-1])))
        fracs.append(float((~moving).mean()))
    print(f"  static   : {np.mean(fracs) * 100:.0f}% of ticks   "
          f"dead-still head {min(heads)}-{max(heads)} ticks, tail {min(tails)}-{max(tails)}")
    if max(tails) - min(tails) > 15:
        problems.append(f"tail dead-time varies {min(tails)}-{max(tails)} ticks "
                        f"({(max(tails) - min(tails)) / p.info['fps']:.1f}s spread) -- "
                        f"contradictory labels at the same static pose")

    # Start-pose consistency: the policy always begins from whatever it is handed.
    starts = np.stack([s[0] for s in p.per_ep(p.st[:, dcols])])
    spread = starts.max(0) - starts.min(0)
    print("  start    : pose spread over episodes " +
          " ".join(f"{p.names[i].split('_', 2)[2].replace('.pos', '')}={v:.0f}"
                   for i, v in zip(dcols, spread)))

    for msg in problems:
        print(f"  ! {msg}")
    return {"eps": p.eps, "dcols": dcols, "starts": starts,
            "ends": np.stack([s[-1] for s in p.per_ep(p.st[:, dcols])]),
            "names": p.names, "problems": problems, "root": p.root, "fps": p.info["fps"]}


def handoff(o: dict, c: dict, colour: str) -> None:
    """open episode k must end exactly where close episode k begins."""
    print(f"\n--- hand-off {colour}: end of open ep k vs start of close ep k ---")
    if len(o["eps"]) != len(c["eps"]):
        print(f"  ! open has {len(o['eps'])} episodes but close has {len(c['eps'])} -- a cycle was "
              f"ended early with `r` instead of `c`, so the pairing below is meaningless")
    n = min(len(o["eps"]), len(c["eps"]))
    diffs = np.abs(c["starts"][:n] - o["ends"][:n]).max(1)
    print(f"  max |diff| over {n} cycles: worst {diffs.max():.2f}, mean {diffs.mean():.2f}")
    bad = [i for i, d in enumerate(diffs) if d > 2.0]
    if bad:
        print(f"  ! cycles {bad} are discontinuous (>2 units) -- "
              f"those two datasets do not join up")
    else:
        print("  continuous: close starts where open ends in every cycle")


def check_images(root: Path, n: int = 3) -> None:
    """Decode a few frames: catches blank, duplicated and truncated images."""
    import io
    from PIL import Image
    files = sorted(glob.glob(str(root / "data" / "**" / "*.parquet"), recursive=True))
    t = pq.read_table(files[0], columns=["observation.images.head", "observation.images.wrist"])
    out = []
    for key in ("head", "wrist"):
        col = t.column(f"observation.images.{key}")
        idx = np.linspace(0, t.num_rows - 1, n, dtype=int)
        imgs = []
        for i in idx:
            v = col[int(i)].as_py()
            raw = v["bytes"] if isinstance(v, dict) else v
            imgs.append(np.asarray(Image.open(io.BytesIO(raw)).convert("RGB")))
        a = np.stack(imgs).astype(np.float32)
        # std across frames tells us the view changes; mean tells us it is not black
        out.append(f"{key}: {a.shape[1]}x{a.shape[2]} mean {a.mean():.0f} "
                   f"frame-to-frame std {a.std(0).mean():.1f}")
        if a.mean() < 5:
            out.append(f"  ! {key} is nearly black")
        if a.std(0).mean() < 1:
            out.append(f"  ! {key} barely changes across the episode -- camera frozen?")
    print("  images   : " + " | ".join(out))


def gripper_events(g: np.ndarray, frac: float = 0.35) -> tuple[int | None, int | None]:
    """Locate the grasp and the release in one pick episode from the gripper trace.

    The gripper is not a two-state signal. A run reads: rest ~1, opened to approach the cube
    ~35-49, closed on the cube ~5, opened again to drop it ~18-37, back to rest ~1. So "closed"
    alone cannot separate the transport from the rest periods that bracket it -- the transitions
    have to be read in order.

    `frac` is deliberately 0.35, not 0.5: some releases only open the gripper to ~18 while the
    approach opened it to ~45, and a half-of-max threshold silently drops those episodes from the
    statistics entirely (it dropped 3 of 18 per colour, and the survivors were biased).
    """
    hi = g > frac * g.max()

    def first(pred, start):
        for i in range(start, len(g)):
            if pred(i):
                return i
        return None

    approach = first(lambda i: hi[i], 0)
    if approach is None:
        return None, None
    grasp = first(lambda i: not hi[i], approach)
    if grasp is None:
        return None, None
    return grasp, first(lambda i: hi[i], grasp)


def pick_separation() -> None:
    """Is the target drawer visible in the pick trajectories at all?

    For `pick` the arm starts and ends at its rest pose whichever drawer is the target, so
    comparing endpoints (as the open/close check does) wrongly reports the colours as identical.
    What carries the drawer identity is where the cube is let go. And the grasp pose is the
    control: it must vary a lot within a colour (nine grid cells) and not at all between colours
    (where the cube starts has nothing to do with where it is going).
    """
    print("\n--- pick: grasp and release poses per colour ---")
    left = None
    data = {}
    for colour in COLOURS:
        p = Phase(f"pick_{colour}")
        if not p.ok:
            return
        left = p.cols("left_arm")
        names = [p.names[i].split("_", 2)[2].replace(".pos", "") for i in left]
        gi = names.index("gripper")
        grasps, releases, missed = [], [], 0
        for s in p.per_ep(p.st[:, left]):
            a, b = gripper_events(s[:, gi])
            if a is None or b is None:
                missed += 1
                continue
            grasps.append(s[a])
            releases.append(s[b])
        data[colour] = {"grasp": np.stack(grasps), "release": np.stack(releases)}
        note = f"  ({missed} episode(s) had no clean grasp/release)" if missed else ""
        print(f"  {colour:7s} {len(grasps)}/{len(p.eps)} episodes analysed{note}")

    for what, expect in (("grasp", "must VARY within a colour, NOT between"),
                         ("release", "must DIFFER between colours")):
        print(f"\n  {what.upper()} -- {expect}")
        means = np.stack([d[what].mean(0) for d in data.values()])
        within = np.mean([d[what].std(0) for d in data.values()], axis=0)
        between = means.max(0) - means.min(0)
        ratio = between / np.maximum(within, 1e-3)
        for colour, d in data.items():
            print(f"    {colour:7s} " + " ".join(f"{n}={v:6.1f}" for n, v in
                                                 zip(names, d[what].mean(0))))
        print(f"    {'within':7s} " + " ".join(f"{n}={v:6.1f}" for n, v in zip(names, within)))
        print(f"    {'between':7s} " + " ".join(f"{n}={v:6.1f}" for n, v in zip(names, between)))
        print(f"    {'ratio':7s} " + " ".join(f"{n}={v:6.2f}" for n, v in zip(names, ratio)))
        j = int(ratio.argmax())
        print(f"    -> strongest: {names[j]} at {ratio[j]:.2f}x")
        if what == "release":
            # The adjacent pairs are the hard ones; the extremes always look separable.
            cs = list(data)
            for a in range(len(cs)):
                for b in range(a + 1, len(cs)):
                    x, y = data[cs[a]]["release"][:, j], data[cs[b]]["release"][:, j]
                    pooled = (x.std() + y.std()) / 2
                    ov = max(0.0, min(x.max(), y.max()) - max(x.min(), y.min()))
                    print(f"       {cs[a]:7s} vs {cs[b]:7s}  gap "
                          f"{abs(x.mean() - y.mean()):5.1f} = "
                          f"{abs(x.mean() - y.mean()) / pooled:4.2f} sigma, "
                          f"range overlap {ov:4.1f}")
            if ratio[j] < 2:
                print("    ! the colours are barely distinguishable -- a policy that ignores "
                      "which drawer to use would land between them and miss all three")


def main() -> None:
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    ap.add_argument("datasets", nargs="*", help="e.g. open_yellow; default is everything present")
    ap.add_argument("--phase", choices=PHASES, help="restrict to one phase")
    ap.add_argument("--no-images", action="store_true", help="skip decoding image samples")
    args = ap.parse_args()

    if args.datasets:
        wanted = args.datasets
    else:
        wanted = [f"{ph}_{c}" for c in COLOURS for ph in PHASES
                  if (DATA / f"xlerobot_{ph}_{c}").exists()]
        if args.phase:
            wanted = [w for w in wanted if w.startswith(f"{args.phase}_")]
    if not wanted:
        sys.exit("nothing to check")

    results = {}
    for name in wanted:
        p = Phase(name)
        r = report(p)
        if r and not args.no_images:
            check_images(p.root)
        results[name] = r

    for colour in COLOURS:
        o, c = results.get(f"open_{colour}"), results.get(f"close_{colour}")
        if o and c:
            handoff(o, c, colour)

    # The three colours are three different drawers in three different places. If their
    # trajectories are indistinguishable, three separate models are learning the same motion and
    # the split buys nothing.
    if all(results.get(f"pick_{c}") for c in COLOURS):
        pick_separation()

    for phase in PHASES:
        if phase == "pick":
            continue          # start/end poses say nothing here -- see pick_separation()
        have = {c: r for c in COLOURS if (r := results.get(f"{phase}_{c}"))}
        if len(have) < 2:
            continue
        print(f"\n--- {phase}: are the three drawers actually distinguishable? ---")
        # Which end of the motion carries the drawer identity depends on the phase: `open` finishes
        # at the drawer, `close` starts there and then retracts to the same home pose for every
        # colour. Judging `close` by its end pose would wrongly say the colours are identical.
        for which in ("starts", "ends"):
            poses = np.stack([r[which].mean(0) for r in have.values()])
            between = float(np.abs(poses.max(0) - poses.min(0)).max())
            within = float(np.mean([r[which].std(0) for r in have.values()], axis=0).max())
            label = "start" if which == "starts" else "end  "
            print(f"  {label} pose: spread between colours {between:5.1f}   "
                  f"within one colour {within:4.1f}   "
                  f"-> {'separable' if between > 3 * within else 'NOT separable'}")
            if which == "starts" and phase == "open" or which == "ends" and phase != "open":
                continue
            for c, r in have.items():
                print(f"    {c:7s} " + " ".join(
                    f"{r['names'][i].split('_', 2)[2].replace('.pos', '')}={v:7.1f}"
                    for i, v in zip(r["dcols"], r[which].mean(0))))

    print("\n=== summary ===")
    bad = {k: r["problems"] for k, r in results.items() if r and r["problems"]}
    for k, r in results.items():
        n = len(r["eps"]) if r else 0
        print(f"  {k:16s} {n:2d} episodes" + ("" if not (r and r['problems'])
                                              else f"   <- {len(r['problems'])} issue(s)"))
    if not bad:
        print("  no issues found")


if __name__ == "__main__":
    main()
