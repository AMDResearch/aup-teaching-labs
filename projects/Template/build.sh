#!/usr/bin/env bash
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

set -euo pipefail

usage() {
    cat <<'HELP'
Usage: bash build.sh [options]

Build a course image on the AUPLC base release named in base-version.txt.

Options:
  --image-name NAME   Local or registry-qualified image name (default: my-course)
  --version VERSION   Image version (default: local)
  --gpu-target TARGET cpu, gfx110x, gfx1150, gfx1151, gfx1152, gfx120x (default: gfx1151)
  --base-image IMAGE  Override base-version.txt with a full base reference or digest
  --push              Push only after a successful build
  -h, --help          Show this help
HELP
}

image_name='my-course'
version='local'
gpu_target='gfx1151'
base_image=''
push=false

while (($#)); do
    case "$1" in
        --image-name|--version|--gpu-target|--base-image)
            if (($# < 2)) || [[ -z "$2" || "$2" == --* ]]; then
                printf 'Missing value for %s\n' "$1" >&2
                exit 2
            fi
            case "$1" in
                --image-name) image_name=$2 ;;
                --version) version=$2 ;;
                --gpu-target) gpu_target=$2 ;;
                --base-image) base_image=$2 ;;
            esac
            shift 2
            ;;
        --push) push=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$gpu_target" in
    cpu|gfx110x|gfx1150|gfx1151|gfx1152|gfx120x) ;;
    *) printf 'Unsupported GPU target: %s\n' "$gpu_target" >&2; exit 2 ;;
esac

# Absolute context allows invocation from any directory.
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

if [[ -z "$base_image" ]]; then
    base_version=$(sed -e 's/#.*//' -e 's/[[:space:]]//g' "$script_dir/base-version.txt" | grep -v '^$' || true)
    if [[ ! "$base_version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]]; then
        printf 'Set base-version.txt to one released Learning Cloud version (vX.Y.Z), or pass --base-image.\n' >&2
        exit 2
    fi
    if [[ "$gpu_target" == cpu ]]; then
        base_image="ghcr.io/amdresearch/auplc-default:${base_version}"
    else
        base_image="ghcr.io/amdresearch/auplc-base:${base_version}-${gpu_target}"
    fi
fi

course_name=$(basename -- "$script_dir")
image_ref="${image_name}:${version}-${gpu_target}"
printf 'Base:   %s\nCourse: %s\nImage:  %s\n' "$base_image" "$course_name" "$image_ref"

if ! docker build --build-arg "BASE_IMAGE=$base_image" --build-arg "COURSE_NAME=$course_name" \
    -t "$image_ref" "$script_dir"; then
    printf 'Course image build failed; push was not attempted.\n' >&2
    exit 1
fi

if [[ "$push" == true ]]; then
    if ! docker push "$image_ref"; then
        printf 'Course image push failed.\n' >&2
        exit 1
    fi
fi
