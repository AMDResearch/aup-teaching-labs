<!-- Copyright (C) 2025 Advanced Micro Devices, Inc. All rights reserved. -->
<!--
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
-->

# Course image template

Keep the course materials, package lists, Dockerfile and build scripts together in
`aup-teaching-labs/projects/<Course>/`. The image inherits a specific released base from
[AUP Learning Cloud](https://github.com/AMDResearch/aup-learning-cloud).
Learning Cloud maintains the base, Jupyter runtime and ROCm/PyTorch stack.
This course adds its own dependencies and materials.

## Create a course

From the teaching-labs repository root:

```bash
cp -R projects/Template projects/MyCourse
```

PowerShell:

```powershell
Copy-Item -LiteralPath projects/Template -Destination projects/MyCourse -Recurse
```

Course authors normally edit only these files; the Dockerfile does not need changes:

| Path | Purpose |
| --- | --- |
| `course_data/` | Course materials (notebooks, code, small datasets, images). Its contents are copied to `/opt/workspace/<Course>/`, which is the student's landing directory. |
| `requirements.txt` | Python packages, installed with `pip install -r`. Pin versions (`package==x.y.z`). |
| `apt-packages.txt` | Ubuntu system packages, installed with `apt-get install`. One per line; `#` starts a comment. |
| `base-version.override.txt` | Optional. Pins the course to one AUP Learning Cloud release (`vX.Y.Z`) while it cannot use the shared base; see below. Do not add it by default. |
| `README.md` | Course description and validation notes for maintainers (not copied into the image). |

`<Course>` is the course directory name; the build scripts pass it to the Dockerfile
automatically. Leave either package file empty when nothing extra is needed.

Package rules:

- Do not list `torch`, `torchvision` or `torchaudio`. The base image's ROCm wheels are
  locked during the build; a requirement that replaces them fails the build.
- Check what the base already provides before adding packages:
  `docker run --rm --entrypoint python3 <base-image> -m pip list`.
- The build runs `pip check`, so conflicting requirements fail the build.
- Some Python packages need system libraries, e.g. `opencv-python` needs `libgl1` and
  `libglib2.0-0` in `apt-packages.txt`.

Only `course_data/`, the two package lists and the Dockerfile enter the build
context (see `.dockerignore`). Keep large datasets and model weights out of Git;
document how the course downloads them.

The included `course_data/notebooks/01-smoke-test.ipynb` checks basic Python execution
and course directory write access. Replace or extend it with representative course
exercises.

## Build only the course image

```bash
bash projects/MyCourse/build.sh --image-name my-course --version local --gpu-target gfx1151
```

```powershell
./projects/MyCourse/build.ps1 -ImageName my-course -Version local -GpuTarget gfx1151
```

Both commands produce `my-course:local-gfx1151`. They use the course directory as
the build context, including when invoked from another working directory.

Courses do not choose their base release. The scripts and teaching-labs CI read the
major version in `projects/course-base-major.txt` (for example `v0`) and build on the
floating tag for it: `ghcr.io/amdresearch/auplc-base:<major>-<target>`, or
`ghcr.io/amdresearch/auplc-default:<major>` for `cpu`. Learning Cloud moves that tag
to its newest final release in the major, so a course always builds on the current
compatible base. The major changes only when Learning Cloud releases an incompatible
base; that change is one reviewed edit to `projects/course-base-major.txt`, made
together with any course fixes.

Learning Cloud picks the exact release for the published course images in its own
`projects/course-base-version.txt`, so a course may be built on a slightly older
release there than in teaching-labs CI. CI writes the resolved base and digest to the
job summary and the `org.opencontainers.image.base.name` and
`org.opencontainers.image.base.digest` labels; to reproduce a past CI build, run the
workflow manually with that digest in `base-image-ref`.

If a course cannot run on the current base yet, add `base-version.override.txt` to the
course directory with one release such as `v0.1.1`. The scripts, teaching-labs CI and
Learning Cloud then build that course on the pinned release and print a warning.
Remove the file as soon as the course works on the shared base.

`--base-version` / `-BaseVersion` uses another release or major for one build, and
`--base-image` / `-BaseImage` a full reference or digest, for example to try a locally
built base. If the floating tag for your target is not published yet, pass a release
with `--base-version`. Official images never use these options.

`--gpu-target` / `-GpuTarget` labels the course image tag; it does not select or
build the base. Supported labels: `cpu`, `gfx110x`, `gfx1150`, `gfx1151`
(default), `gfx1152`, `gfx120x`. For CPU courses, supply a versioned
`ghcr.io/amdresearch/auplc-default` image and select `cpu`.
The base must support the target hardware and the build host must match the
intended CPU architecture. Changing a tag does not convert architectures.

These scripts build the course image using the published base. Base changes belong
in AUP Learning Cloud. Accept the chosen base build as the course's dependency;
validate the course-specific dependencies and representative notebooks on it.

Official course images are published by AUP Learning Cloud, not by these scripts or by
teaching-labs CI. After a course change merges into teaching-labs `develop`, Learning
Cloud updates its `aup-teaching-labs` submodule, builds the changed courses for its dev
cluster and later promotes the tested image digests to production. Teaching-labs CI
only builds and checks pull requests.

## Validate the course

Check Jupyter, dependencies, execution and write access:

```bash
docker run --rm --entrypoint /bin/sh my-course:local-gfx1151 -c 'id && pwd && jupyter lab --version && python3 -m pip check && jupyter nbconvert --to notebook --execute notebooks/01-smoke-test.ipynb --output /tmp/course-smoke.ipynb'
```

Open the course locally:

```bash
docker run --rm -it -p 127.0.0.1:8888:8888 --entrypoint jupyter my-course:local-gfx1151 lab --ip=0.0.0.0 --no-browser
```

Use the token URL printed by Jupyter. GPU courses should also run with
`--device=/dev/kfd --device=/dev/dri --group-add video` on a configured Linux AMD GPU
host. Execute representative notebooks and verify course imports, data loading and
GPU operations on the matching hardware. Add dataset/model setup instructions
needed by the course.

Acceptance covers a successful image build, Jupyter startup and representative
course execution. The sample notebook alone does not validate a course or GPU.
Deploying Learning Cloud and changing site values are outside this template.
No real image build or notebook execution has been performed for this draft.

## Optional push

Pushing is for personal testing only; official images come from Learning Cloud.
Choose a registry-qualified image name in your own namespace, then add `--push` or
`-Push` after logging in to that registry:

```bash
bash projects/MyCourse/build.sh --image-name ghcr.io/YOUR_ORG/my-course --version v0.1.0 --gpu-target gfx1151 --push
```

```powershell
./projects/MyCourse/build.ps1 -ImageName ghcr.io/YOUR_ORG/my-course -Version v0.1.0 -GpuTarget gfx1151 -Push
```

A failed build stops the script before push. Registry access and package visibility
are managed by the course maintainer.
