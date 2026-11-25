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

# VS Code (code-server) course image template

Use this template for courses that students work on in VS Code in the browser
instead of JupyterLab. It follows the same rules as [`../Template`](../Template/README.md):
course materials, package lists, Dockerfile and build scripts live together in
`aup-teaching-labs/projects/<Course>/`, and the image inherits a specific released
base from [AUP Learning Cloud](https://github.com/AMDResearch/aup-learning-cloud).

The difference is the base. This template builds on the Learning Cloud code images,
which already contain code-server, the nginx front end that serves it under the Hub
prefix, the `start-code-server.sh` startup script, Node.js, pixi and a default set of
VS Code extensions:

| Target | Base image |
| --- | --- |
| GPU (`gfx110x`, `gfx1150`, `gfx1151`, `gfx1152`, `gfx120x`) | `ghcr.io/amdresearch/auplc-code-gpu:<version>-<target>` (built on `auplc-base`, so ROCm and ROCm PyTorch are included) |
| `cpu` | `ghcr.io/amdresearch/auplc-code-cpu:<version>` (built on `auplc-default`) |

The course image keeps the base startup command, so code-server starts exactly as in
the Learning Cloud `code-gpu` / `code-cpu` environments.

## Create a course

From the teaching-labs repository root:

```bash
cp -R projects/Template-Code projects/MyCodeCourse
```

PowerShell:

```powershell
Copy-Item -LiteralPath projects/Template-Code -Destination projects/MyCodeCourse -Recurse
```

Course authors normally edit only these files; the Dockerfile does not need changes:

| Path | Purpose |
| --- | --- |
| `course_data/` | Course materials (source code, small datasets, images, `.vscode/` workspace settings and tasks). Its contents are copied to `/opt/workspace/<Course>/`, the folder code-server opens. |
| `extensions.txt` | Extra VS Code extensions from Open VSX, one ID per line, pinned as `publisher.name@x.y.z`. |
| `requirements.txt` | Python packages, installed with `pip install -r`. Pin versions (`package==x.y.z`). |
| `apt-packages.txt` | Ubuntu system packages, installed with `apt-get install`. One per line; `#` starts a comment. |
| `base-version.override.txt` | Optional. Pins the course to one AUP Learning Cloud release (`vX.Y.Z`) while it cannot use the shared base; see below. Do not add it by default. |
| `README.md` | Course description and validation notes for maintainers (not copied into the image). |

`<Course>` is the course directory name; the build scripts pass it to the Dockerfile
automatically. Leave any list empty when nothing extra is needed.

Package and extension rules:

- The Python package rules of [`../Template`](../Template/README.md) apply: on GPU
  bases do not list `torch`, `torchvision` or `torchaudio`, and conflicting
  requirements fail `pip check`. The `cpu` base has no PyTorch; a CPU course that
  needs it lists CPU wheels, for example
  `--extra-index-url https://download.pytorch.org/whl/cpu` and `torch==2.9.1+cpu`.
- Extensions are installed as built-in extensions in
  `/usr/lib/code-server/lib/vscode/extensions`, like the base's own, so every student
  gets them without anything written to their home directory. Students can still
  install more extensions for themselves.
- Do not list an extension the base already provides; a duplicate extension ID fails
  the build. List the base's extensions with
  `docker run --rm --entrypoint ls <base-image> /usr/lib/code-server/lib/vscode/extensions`.
- Extensions come from [Open VSX](https://open-vsx.org/), code-server's marketplace.
  Microsoft-only extensions that are not published there cannot be installed.

The included `course_data/smoke_test.py` checks Python execution and course directory
write access; `--gpu` also runs a GPU matrix multiplication and compares it with the
CPU result. `course_data/.vscode/tasks.json` exposes both as VS Code tasks
(**Terminal → Run Task…**). Replace or extend them with representative course
exercises.

## Build only the course image

```bash
bash projects/MyCodeCourse/build.sh --image-name my-code-course --version local --gpu-target gfx1151
```

```powershell
./projects/MyCodeCourse/build.ps1 -ImageName my-code-course -Version local -GpuTarget gfx1151
```

Both commands produce `my-code-course:local-gfx1151`. The base comes from the major
version in `projects/course-base-major.txt` combined with `--gpu-target` /
`-GpuTarget`, or from an optional `base-version.override.txt`, exactly as in
[`../Template`](../Template/README.md#build-only-the-course-image).
`--base-version` / `-BaseVersion` and `--base-image` / `-BaseImage` select another
base for one build. Use a code image with `--base-image`; an `auplc-base` image has no
code-server, so the course would not start in VS Code.

Official images are published by AUP Learning Cloud, not by these scripts or by
teaching-labs CI. Teaching-labs CI builds a course on `auplc-base` unless its
`.github/build-config.json` entry sets `base_image`; a code-server course selects the
code image repository there. `"cpu": true` builds only the `cpu` target, on
`auplc-default` unless `base_image` is set:

```json
{"name": "MyCodeCourse", "image": "auplc-mycodecourse", "context": "projects/MyCodeCourse", "base_image": "ghcr.io/amdresearch/auplc-code-gpu"}
{"name": "MyCpuCourse", "image": "auplc-mycpucourse", "context": "projects/MyCpuCourse", "base_image": "ghcr.io/amdresearch/auplc-code-cpu", "cpu": true}
```

CI combines that repository with the major version (or the course override) as
`<version>-<target>` for the default GPU target, or `<version>` for a `cpu` course, and
resolves it without registry credentials, so the code image must be publicly readable
and published with that tag.

## Validate the course

Check dependencies, execution and write access:

```bash
docker run --rm --entrypoint /bin/sh my-code-course:local-gfx1151 -c 'id && pwd && code-server --version && python3 -m pip check && python3 smoke_test.py'
```

Open the course locally in VS Code:

```bash
docker run --rm -it -p 127.0.0.1:8888:8888 my-code-course:local-gfx1151
```

Then open <http://127.0.0.1:8888/>. The base startup script serves code-server
without a password; only bind it to `127.0.0.1`. GPU courses should also run with
`--device=/dev/kfd --device=/dev/dri --group-add video` (plus the render group of the
host) on a configured Linux AMD GPU host, then run `python3 smoke_test.py --gpu` in the
VS Code terminal. Execute representative course exercises on the matching hardware.

Acceptance covers a successful image build, code-server startup with the course
folder and extensions, and representative course execution. The smoke test alone
does not validate a course or GPU.

## Register the course in Learning Cloud

Deploying and changing site values are outside this template, but a code-server
course needs these resource metadata fields so the Hub starts code-server instead of
JupyterLab and opens the course folder:

```yaml
custom:
  resources:
    metadata:
      Course-MyCodeCourse:
        resourceType: "browser-ide"
        launchMode: "code-server"
        defaultPath: "/opt/workspace/MyCodeCourse"
```

`/opt/workspace/<Course>` is part of the image, so student changes there are lost
when the server stops. Ask students to keep their work in `/home/jovyan`, or have the
course copy its starter files there.

## Optional push

Pushing is for personal testing only; official images come from Learning Cloud.
Choose a registry-qualified image name in your own namespace, then add `--push` or
`-Push` after logging in to that registry:

```bash
bash projects/MyCodeCourse/build.sh --image-name ghcr.io/YOUR_ORG/my-code-course --version v0.1.0 --gpu-target gfx1151 --push
```

```powershell
./projects/MyCodeCourse/build.ps1 -ImageName ghcr.io/YOUR_ORG/my-code-course -Version v0.1.0 -GpuTarget gfx1151 -Push
```

A failed build stops the script before push.
