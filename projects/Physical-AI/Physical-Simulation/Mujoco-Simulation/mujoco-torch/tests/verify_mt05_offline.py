#!/usr/bin/env python3
# Copyright (C) 2025 Advanced Micro Devices, Inc. All rights reserved.
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Validate that MT05's pinned Hugging Face cache works with no network access."""

import argparse
import gc
import os
import sys
from pathlib import Path


EXPECTED_REVISIONS = {
    "lerobot/smolvla_base": "c83c3163b8ca9b7e67c509fffd9121e66cb96205",
    "HuggingFaceTB/SmolVLM2-500M-Video-Instruct": "7b375e1b73b11138ff12fe22c8f2822d8fe03467",
    "sonya-tw/mt05-smolvla-lift": "90bfedc7c7fc76d04967a50c9b2267517fc96615",
}
POLICY_ALLOW_PATTERNS = [
    "config.json",
    "model.safetensors",
    "policy_preprocessor.json",
    "policy_preprocessor_step_5_normalizer_processor.safetensors",
    "policy_postprocessor.json",
    "policy_postprocessor_step_0_unnormalizer_processor.safetensors",
]
SMOLVLM_ALLOW_PATTERNS = [
    "model.safetensors",
    "config.json",
    "generation_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "added_tokens.json",
    "chat_template.json",
    "chat_template.jinja",
    "processor_config.json",
    "preprocessor_config.json",
    "video_preprocessor_config.json",
    "vocab.json",
    "merges.txt",
    "tokenizer.model",
]
REPOSITORY_ALLOW_PATTERNS = {
    "lerobot/smolvla_base": POLICY_ALLOW_PATTERNS,
    "HuggingFaceTB/SmolVLM2-500M-Video-Instruct": SMOLVLM_ALLOW_PATTERNS,
    "sonya-tw/mt05-smolvla-lift": POLICY_ALLOW_PATTERNS,
}
POLICY_REPOSITORIES = (
    "lerobot/smolvla_base",
    "sonya-tw/mt05-smolvla-lift",
)


def default_cache_dir() -> Path:
    configured_cache = os.environ.get("HF_HUB_CACHE")
    if configured_cache:
        return Path(configured_cache)
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    return hf_home / "hub"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate MT05's pinned Hugging Face cache and load both SmolVLA policies "
            "and processor pipelines with Hugging Face and Transformers forced offline. "
            "Run this inside the image with network access disabled."
        )
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=default_cache_dir(),
        help="Native Hugging Face hub cache directory (default: $HF_HUB_CACHE or $HF_HOME/hub).",
    )
    return parser.parse_args()


def repository_cache_dir(cache_dir: Path, repository: str) -> Path:
    return cache_dir / f"models--{repository.replace('/', '--')}"


def validate_pinned_snapshots(cache_dir: Path) -> None:
    for repository, revision in EXPECTED_REVISIONS.items():
        repository_dir = repository_cache_dir(cache_dir, repository)
        main_ref = repository_dir / "refs" / "main"
        snapshot_dir = repository_dir / "snapshots" / revision

        if not main_ref.is_file():
            raise RuntimeError(f"{repository} is missing the native cache ref {main_ref}")
        actual_revision = main_ref.read_text(encoding="utf-8").strip()
        if actual_revision != revision:
            raise RuntimeError(
                f"{repository} refs/main is {actual_revision!r}, expected pinned revision {revision}"
            )
        if not snapshot_dir.is_dir():
            raise RuntimeError(f"{repository} is missing pinned snapshot {snapshot_dir}")


def validate_symlinks_and_onnx(cache_dir: Path) -> None:
    broken_symlinks = []
    onnx_paths = []
    for path in cache_dir.rglob("*"):
        if path.is_symlink() and not path.exists():
            broken_symlinks.append(path)
        relative_parts = path.relative_to(cache_dir).parts
        if path.name.lower().endswith(".onnx") or "onnx" in relative_parts:
            onnx_paths.append(path)

    if broken_symlinks:
        formatted_paths = ", ".join(str(path) for path in broken_symlinks[:5])
        raise RuntimeError(f"broken Hugging Face cache symlinks: {formatted_paths}")
    if onnx_paths:
        formatted_paths = ", ".join(str(path) for path in onnx_paths[:5])
        raise RuntimeError(f"ONNX artifacts remain in the Hugging Face cache: {formatted_paths}")


def validate_offline_snapshot_lookup(cache_dir: Path) -> None:
    from huggingface_hub import snapshot_download

    for repository, revision in EXPECTED_REVISIONS.items():
        for lookup_name, lookup_kwargs in (
            ("pinned revision", {"revision": revision}),
            ("refs/main", {"revision": "main"}),
            ("default named lookup", {}),
        ):
            snapshot_dir = Path(
                snapshot_download(
                    repository,
                    cache_dir=cache_dir,
                    local_files_only=True,
                    allow_patterns=REPOSITORY_ALLOW_PATTERNS[repository],
                    **lookup_kwargs,
                )
            )
            if snapshot_dir.name != revision:
                raise RuntimeError(
                    f"{lookup_name} for {repository} returned {snapshot_dir}, expected {revision}"
                )


def is_accelerator_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "cuda",
            "hip",
            "rocm",
            "no gpu",
            "no accelerator",
            "no kernel image",
        )
    )


def load_policies_offline() -> None:
    from lerobot.policies.factory import make_pre_post_processors
    from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy

    for repository in POLICY_REPOSITORIES:
        print(f"Loading {repository} and its processor pipelines offline")
        try:
            policy = SmolVLAPolicy.from_pretrained(repository)
            preprocessor, postprocessor = make_pre_post_processors(
                policy.config,
                pretrained_path=repository,
                preprocessor_overrides={"device_processor": {"device": "cpu"}},
            )
        except Exception as error:
            if is_accelerator_error(error):
                raise RuntimeError(
                    "The cache metadata is valid, but SmolVLA policy construction needs an "
                    "accessible ROCm GPU in this image. Re-run on a GPU-enabled node after "
                    "confirming the container receives /dev/kfd and ROCm devices."
                ) from error
            raise RuntimeError(f"offline policy load failed for {repository}: {error}") from error
        del preprocessor, postprocessor, policy
        gc.collect()


def main() -> int:
    args = parse_args()
    cache_dir = args.cache_dir.resolve()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_CACHE"] = str(cache_dir)

    try:
        validate_pinned_snapshots(cache_dir)
        validate_symlinks_and_onnx(cache_dir)
        validate_offline_snapshot_lookup(cache_dir)
        load_policies_offline()
    except Exception as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print("PASS: MT05 snapshots, both SmolVLA policies, and processor pipelines load offline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
