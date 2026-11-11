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

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

RAW_DIR="raw"
BASE_URL="https://github.com/zalandoresearch/fashion-mnist/raw/master/data/fashion"

FILES=(
  "train-images-idx3-ubyte.gz"
  "train-labels-idx1-ubyte.gz"
  "t10k-images-idx3-ubyte.gz"
  "t10k-labels-idx1-ubyte.gz"
)

mkdir -p "${RAW_DIR}"
cd "${RAW_DIR}"

echo "Downloading FashionMNIST dataset into $(pwd)"

for file in "${FILES[@]}"; do
  if [[ -f "${file%.gz}" ]]; then
    echo "[SKIP] ${file%.gz} already exists"
    continue
  fi

  if [[ ! -f "${file}" ]]; then
    echo "[DOWNLOAD] ${file}"
    wget -q --show-progress "${BASE_URL}/${file}"
  else
    echo "[FOUND] ${file}"
  fi

  echo "[EXTRACT] ${file}"
  gunzip -f "${file}"
done

echo "FashionMNIST download complete."
