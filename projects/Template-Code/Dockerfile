# check=skip=InvalidDefaultArgInFrom
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

# VS Code (code-server) course image built on a released AUP Learning Cloud
# code image (auplc-code-gpu / auplc-code-cpu).
#
# Course authors normally edit only:
#   apt-packages.txt  system packages (apt-get install)
#   requirements.txt  Python packages (pip install -r)
#   extensions.txt    VS Code extensions from Open VSX
#   course_data/      materials copied to /opt/workspace/<COURSE_NAME>/
#   base-version.txt  Learning Cloud release used as BASE_IMAGE
#
# The base already provides code-server, its nginx front end and start script,
# Node.js, pixi, the jovyan user (UID 1000, GID 100) and, for GPU targets, ROCm
# and ROCm PyTorch.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Folder under /opt/workspace and the folder code-server opens. build.sh /
# build.ps1 set it to the course directory name.
ARG COURSE_NAME=MyCourse
ARG BASE_IMAGE
LABEL org.opencontainers.image.base.name="${BASE_IMAGE}"

USER root

# 1. System packages: one package per line; '#' starts a comment.
COPY apt-packages.txt /tmp/auplc-course/apt-packages.txt
RUN set -eu; \
    packages="$(tr -d '\r' < /tmp/auplc-course/apt-packages.txt | sed -e 's/#.*//' | xargs)"; \
    if [ -n "${packages}" ]; then \
        apt-get update; \
        apt-get install -y --no-install-recommends ${packages}; \
        apt-get clean; \
        rm -rf /var/lib/apt/lists/*; \
    else \
        echo "apt-packages.txt lists no packages; skipping apt-get install"; \
    fi

# 2. Python packages. The base image's ROCm torch wheels are locked through a
# constraints file so pip cannot replace them with CUDA/CPU wheels from PyPI.
# CPU bases have no torch, so a course may add its own.
COPY requirements.txt /tmp/auplc-course/requirements.txt
RUN set -eu; \
    python3 -m pip freeze | grep -iE '^(torch|torchvision|torchaudio)==' > /tmp/auplc-course/constraints.txt || true; \
    if grep -qvE '^[[:space:]]*(#|$)' /tmp/auplc-course/requirements.txt; then \
        python3 -m pip install --no-cache-dir \
            -c /tmp/auplc-course/constraints.txt \
            -r /tmp/auplc-course/requirements.txt; \
    else \
        echo "requirements.txt lists no packages; skipping pip install"; \
    fi; \
    if [ -s /tmp/auplc-course/constraints.txt ]; then \
        python3 -m pip freeze | grep -iE '^(torch|torchvision|torchaudio)==' | diff /tmp/auplc-course/constraints.txt -; \
    fi; \
    python3 -m pip check; \
    rm -rf /tmp/auplc-course

# 3. VS Code extensions: one ID per line, optionally pinned as publisher.name@x.y.z.
# They are installed as built-in extensions, like the base image's own, so they
# are available to every user without touching the home directory. An extension
# ID that the base already provides fails the build.
COPY extensions.txt /tmp/auplc-course/extensions.txt
RUN set -eu; \
    system_root=/usr/lib/code-server/lib/vscode/extensions; \
    staging=/tmp/auplc-course/extensions; \
    mkdir -p "${staging}"; \
    python3 -c 'import json, pathlib, sys; builtin = {(str(m.get("publisher")) + "." + str(m.get("name"))).lower() for m in (json.loads(p.read_text(encoding="utf-8")) for p in pathlib.Path(sys.argv[1]).glob("*/package.json"))}; wanted = {line.split("#")[0].strip().split("@")[0].lower() for line in open(sys.argv[2], encoding="utf-8")}; dup = sorted(wanted & builtin); dup and sys.exit("extensions.txt lists extensions the base already provides: " + ", ".join(dup))' "${system_root}" /tmp/auplc-course/extensions.txt; \
    tr -d '\r' < /tmp/auplc-course/extensions.txt | sed -e 's/#.*//' | xargs -r -n 1 \
        code-server --user-data-dir /tmp/auplc-course/user-data \
            --extensions-dir "${staging}" --install-extension; \
    for entry in "${staging}"/*/; do \
        [ -d "${entry}" ] || continue; \
        name="$(basename "${entry}")"; \
        if [ -e "${system_root}/${name}" ]; then \
            echo "extension directory already exists in the base: ${name}" >&2; \
            exit 1; \
        fi; \
        cp -a -- "${entry}" "${system_root}/${name}"; \
        chown -R root:root "${system_root}/${name}"; \
        chmod -R u=rwX,go=rX "${system_root}/${name}"; \
    done; \
    python3 -c 'import collections, json, pathlib, sys; manifests = [json.loads(p.read_text(encoding="utf-8")) for p in pathlib.Path(sys.argv[1]).glob("*/package.json")]; ids = collections.Counter((str(m.get("publisher")) + "." + str(m.get("name"))).lower() for m in manifests); dup = sorted(i for i, n in ids.items() if n > 1); dup and sys.exit("duplicate VS Code extension IDs: " + ", ".join(dup))' "${system_root}"; \
    rm -rf /tmp/auplc-course

# 4. Course materials.
COPY --chown=1000:100 course_data/ /opt/workspace/${COURSE_NAME}/

USER jovyan
WORKDIR /opt/workspace/${COURSE_NAME}
# Inherit the base image's code-server startup command. Without a Hub override
# (AUPLC_CODE_WORKDIR), code-server opens this WORKDIR.
