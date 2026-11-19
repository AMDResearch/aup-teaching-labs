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
"""Course smoke test: Python, write access and, with --gpu, a GPU computation.

Run it from the VS Code terminal or with the "Course: smoke test" task, then
replace or extend it with representative course exercises.
"""

import argparse
import sys
import tempfile
from pathlib import Path


def check_gpu() -> None:
    import torch

    if not torch.cuda.is_available():
        sys.exit("No GPU visible to PyTorch; run on a GPU target with /dev/kfd and /dev/dri.")
    a = torch.randn(256, 256)
    b = torch.randn(256, 256)
    result = (a.cuda() @ b.cuda()).cpu()
    torch.testing.assert_close(result, a @ b, rtol=1e-3, atol=1e-3)
    print("GPU:", torch.cuda.get_device_name(0))
    print("HIP:", torch.version.hip)
    print("GPU matrix multiplication matches the CPU result")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu", action="store_true", help="also run a GPU computation")
    args = parser.parse_args()

    course_dir = Path(__file__).resolve().parent
    with tempfile.TemporaryFile(dir=course_dir) as probe:
        probe.write(b"course smoke test")
    print("Python:", sys.version)
    print("Writable course directory:", course_dir)
    if args.gpu:
        check_gpu()


if __name__ == "__main__":
    main()
