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
| `base-version.txt` | The AUP Learning Cloud release the course builds on, e.g. `v0.1.0`. |
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

The base comes from `base-version.txt`. Each Learning Cloud release tag `vX.Y.Z`
publishes `ghcr.io/amdresearch/auplc-base:vX.Y.Z-<target>` (and
`ghcr.io/amdresearch/auplc-default:vX.Y.Z` for `cpu`); the scripts combine the version
with `--gpu-target` to select it. The template ships with the placeholder `vX.Y.Z`, so
builds fail until you set a real release such as `v0.1.0`. Do not use `latest` or the
floating `vX.Y` / `vX` versions, which move with later releases. The same file is
read by teaching-labs CI and by Learning Cloud when it publishes the course, so
changing the base is a reviewed change to this file. The course image records the
base in the `org.opencontainers.image.base.name` label.

`--base-image` / `-BaseImage` overrides the file with a full reference or digest, for
example to try a locally built base. Official images always use `base-version.txt`.

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
