#!/usr/bin/env python
"""Verify the environment can actually run this lab. Exits non-zero if it cannot.

    python verify_env.py                # everything: training and the hardware path
    python verify_env.py --train        # only what training needs (the container)
    python verify_env.py --robot        # only what recording and evaluation need

This exists because the errors it replaces are uninformative. The worst is the GPU one:
`--policy.device=cuda` reaches `get_safe_torch_device`, which is a bare `assert
torch.cuda.is_available()` with no message (lerobot/utils/device_utils.py). Without a working
ROCm build you get an empty AssertionError at the bottom of a long stack, hours of dataset
preparation later, saying nothing about what is wrong or how to fix it.

The second one is worse than uninformative because it looks like a broken install: with
LD_PRELOAD pointing at a system HSA runtime, `import torch` dies on an undefined symbol. The
rocm-sdk wheels carry their own runtime and must not have another preloaded over it.

`torch.cuda.is_available()` alone is not enough either -- it can be True on a build whose kernels
then fail for the GPU actually present -- so this runs a real matmul on the device.
"""

import argparse
import importlib
import os
import sys
from pathlib import Path

REQUIRED_FOR = {}          # check name -> {"train", "robot"}
results = []               # (ok, name, detail, hint)


def check(name, roles, hint=""):
    """Register a check. The function returns a detail string, or raises."""
    def wrap(fn):
        REQUIRED_FOR[name] = roles
        results.append((fn, name, hint))
        return fn
    return wrap


@check("no LD_PRELOAD", {"train", "robot"},
       "unset LD_PRELOAD -- the rocm-sdk wheels ship their own HSA runtime (SETUP.md)")
def _preload():
    v = os.environ.get("LD_PRELOAD", "")
    if v:
        raise RuntimeError(f"LD_PRELOAD={v}")
    return "unset"


@check("torch", {"train", "robot"}, "the base image or build_env.sh provides this")
def _torch():
    t = importlib.import_module("torch")
    if "rocm" not in t.__version__:
        raise RuntimeError(f"{t.__version__} is not a ROCm build")
    return t.__version__


# Evaluation needs the GPU as much as training does: chain_eval.py defaults to --device cuda and
# eval.sh hardcodes --policy.device=cuda. Recording alone does not -- record_teleop.py never
# touches torch -- but the host env does both, so this is required for the robot role too.
@check("GPU", {"train", "robot"},
       "docker: --device=/dev/kfd --device=/dev/dri --group-add video "
       "--group-add \"$(getent group render | cut -d: -f3)\"  |  "
       "host: unset LD_PRELOAD and check rocm-smi. Recording alone does not need a GPU; "
       "evaluation does (chain_eval.py --device cpu works but ACT costs ~1.9 s per inference "
       "and SmolVLA ~40 s)")
def _gpu():
    t = importlib.import_module("torch")
    if not t.cuda.is_available():
        raise RuntimeError("torch.cuda.is_available() is False")
    # is_available() only reports that a device was enumerated. Run something on it: a build
    # missing the kernels for this GPU enumerates fine and fails here instead.
    x = t.randn(256, 256, device="cuda")
    float((x @ x).sum())
    return t.cuda.get_device_name(0)


@check("lerobot", {"train", "robot"}, "pip install -e <lerobot checkout>")
def _lerobot():
    m = importlib.import_module("lerobot")
    v = getattr(m, "__version__", "?")
    importlib.import_module("lerobot.scripts.lerobot_train")
    return f"{v}  ({m.__file__})"


@check("deps match source", {"train", "robot"},
       "rebuild the image from the same commit the checkout is on "
       "(docker build --build-arg LEROBOT_REF=<sha>), or pip install the pinned version")
def _deps():
    """Compare the running lerobot source's own pins against what is actually installed.

    The failure this catches is silent. lerobot 0.6.1 on PyPI asks for draccus>=0.11.6 while
    commit 95211b98 pins draccus==0.10.0, and running 0.10.0-era code under 0.11.6 trains
    perfectly -- same losses to three decimals -- while writing a config.json with no top-level
    "type" field. Nothing goes wrong until something tries to load the checkpoint back.
    """
    import tomllib
    from importlib.metadata import version, PackageNotFoundError
    from packaging.requirements import Requirement
    src = Path(importlib.import_module("lerobot").__file__).parents[2] / "pyproject.toml"
    if not src.exists():
        return "not running from a source checkout, nothing to compare"
    deps = tomllib.loads(src.read_text()).get("project", {}).get("dependencies", [])
    bad = []
    for raw in deps:
        r = Requirement(raw)
        if r.marker and not r.marker.evaluate():
            continue
        try:
            have = version(r.name)
        except PackageNotFoundError:
            continue                       # an optional or not-yet-needed dependency
        # Local versions (torch 2.12.0+rocm7.14.0) are deliberately outside lerobot's ranges here.
        if "+" in have:
            continue
        if not r.specifier.contains(have, prereleases=True):
            bad.append(f"{r.name} {have} vs {src.name} wants {r.specifier}")
    if bad:
        raise RuntimeError("; ".join(bad))
    return f"{len(deps)} pins in {src.parent.name}/pyproject.toml satisfied"


@check("xlerobot driver", {"robot"},
       "copy lerobot_patches/xlerobot into <lerobot>/src/lerobot/robots/ (lerobot_patches/README.md)")
def _driver():
    importlib.import_module("lerobot.robots.xlerobot")
    cfg = importlib.import_module("lerobot.robots.config").RobotConfig
    if "xlerobot" not in cfg.get_known_choices():
        raise RuntimeError("driver imports but the CLI does not know it -- utils.patch not applied")
    return "imported and registered with the CLI"


@check("rollout context patch", {"robot"},
       "git apply lerobot_patches/context.patch in the lerobot checkout")
def _context():
    import inspect
    ctx = importlib.import_module("lerobot.rollout.context")
    src = inspect.getsource(ctx.build_rollout_context)
    if ".pos" in src.split("observation_features_hw")[1][:300]:
        raise RuntimeError("the .pos filter is still there -- record is 17d, rollout would be 14d")
    return "applied"


@check("opencv GUI", {"robot"},
       "pip uninstall opencv-python-headless && pip install opencv-python (SETUP.md)")
def _cv2():
    b = importlib.import_module("cv2").getBuildInformation()
    # The field reads "GUI:  QT5" / "GTK+" on a GUI build and "NONE" on the headless one. Do not
    # test for "Qt:" -- that substring is never present, so the test always fails.
    line = next((x for x in b.splitlines() if x.strip().startswith("GUI:")), "")
    gui = line.split(":", 1)[1].strip() if ":" in line else ""
    if not gui or gui == "NONE":
        raise RuntimeError("headless build -- cv2.imshow will raise 'The function is not implemented'")
    return gui


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--train", action="store_true", help="only what training needs")
    ap.add_argument("--robot", action="store_true", help="only what recording and evaluation need")
    # `docker build` has no --device flags, so there is no GPU to find at image build time. The
    # imports are still worth checking there; the GPU gets checked on the first docker run.
    ap.add_argument("--no-gpu", action="store_true", help="skip the GPU check (use during docker build)")
    args = ap.parse_args()
    roles = {"train"} if args.train else {"robot"} if args.robot else {"train", "robot"}

    print(f"python {sys.version.split()[0]}   checking: {', '.join(sorted(roles))}\n")
    failed = []
    for fn, name, hint in results:
        if not REQUIRED_FOR[name] & roles:
            continue
        if args.no_gpu and name == "GPU":
            print(f"  skip  {name:22} --no-gpu")
            continue
        try:
            print(f"  ok    {name:22} {fn()}")
        except Exception as e:
            print(f"  FAIL  {name:22} {type(e).__name__}: {e}")
            failed.append((name, hint))

    # Not a failure either way: only video-backed datasets need it, and the existing ones are
    # all image-backed. It does work here -- the old "ABI mismatch" story was LD_PRELOAD.
    try:
        importlib.import_module("torchcodec")
        print("\n  note  torchcodec present -> video-backed datasets are usable")
    except Exception as e:
        print(f"\n  note  torchcodec unavailable ({type(e).__name__}) -> record with --images-not-video")

    if failed:
        print()
        for name, hint in failed:
            print(f"  ! {name}" + (f"\n      {hint}" if hint else ""))
        sys.exit(f"\n{len(failed)} problem(s) -- not safe to run")
    print("\nenvironment looks good")


if __name__ == "__main__":
    main()
