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

"""Fail when course-added OCI image layers exceed a compressed size budget."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check compressed OCI layer descriptor sizes. Provide either a registry "
            "image manifest JSON file or an OCI layout directory. The first N layers "
            "are classified as inherited; every later layer is classified as course-added."
        )
    )
    parser.add_argument(
        "manifest_or_layout",
        type=Path,
        help="Registry image manifest JSON file or OCI layout directory.",
    )
    parser.add_argument(
        "--inherited-layer-count",
        required=True,
        type=non_negative_int,
        help="Number of leading layer descriptors inherited from the base image.",
    )
    parser.add_argument(
        "--max-course-layer-bytes",
        required=True,
        type=positive_int,
        help="Maximum allowed compressed size in bytes for each course-added layer.",
    )
    return parser.parse_args()


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def read_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON in {path}: {error}") from error

    if not isinstance(document, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return document


def load_manifest(manifest_or_layout: Path) -> dict[str, Any]:
    if manifest_or_layout.is_file():
        manifest = read_json(manifest_or_layout)
        if "layers" not in manifest:
            raise ValueError(
                f"{manifest_or_layout} is not an image manifest; pass an OCI layout "
                "directory rather than its index.json file"
            )
        return manifest

    if not manifest_or_layout.is_dir():
        raise ValueError(f"{manifest_or_layout} is neither a file nor a directory")

    index_path = manifest_or_layout / "index.json"
    index = read_json(index_path)
    descriptors = index.get("manifests")
    if not isinstance(descriptors, list) or not descriptors:
        raise ValueError(f"{index_path} does not contain an OCI manifest descriptor")

    descriptor = descriptors[0]
    if not isinstance(descriptor, dict):
        raise ValueError(f"{index_path} has an invalid first manifest descriptor")
    digest = descriptor.get("digest")
    if not isinstance(digest, str) or ":" not in digest:
        raise ValueError(f"{index_path} has a manifest descriptor without a valid digest")

    algorithm, encoded_digest = digest.split(":", 1)
    return read_json(manifest_or_layout / "blobs" / algorithm / encoded_digest)


def validate_layers(
    manifest: dict[str, Any], inherited_layer_count: int, max_course_layer_bytes: int
) -> list[str]:
    layers = manifest.get("layers")
    if not isinstance(layers, list):
        raise ValueError("image manifest does not contain a layers array")
    if inherited_layer_count > len(layers):
        raise ValueError(
            f"inherited layer count {inherited_layer_count} exceeds {len(layers)} manifest layers"
        )

    failures = []
    for index, descriptor in enumerate(layers):
        if not isinstance(descriptor, dict):
            raise ValueError(f"layer {index} is not a descriptor object")
        size = descriptor.get("size")
        if not isinstance(size, int) or size < 0:
            raise ValueError(f"layer {index} has an invalid compressed size: {size!r}")

        origin = "inherited" if index < inherited_layer_count else "course-added"
        digest = descriptor.get("digest", "<missing digest>")
        print(f"layer {index}: {origin}, {size} compressed bytes, {digest}")
        if origin == "course-added" and size > max_course_layer_bytes:
            failures.append(
                f"layer {index} is {size} compressed bytes, above the course-layer limit "
                f"of {max_course_layer_bytes} bytes"
            )
    return failures


def main() -> int:
    args = parse_args()
    try:
        manifest = load_manifest(args.manifest_or_layout)
        failures = validate_layers(
            manifest, args.inherited_layer_count, args.max_course_layer_bytes
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1

    print("PASS: every course-added layer is within the compressed size limit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
