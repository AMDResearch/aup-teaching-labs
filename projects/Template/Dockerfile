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

# Course image built on a released AUP Learning Cloud base.
#
# Course authors normally edit only:
#   apt-packages.txt  system packages (apt-get install)
#   requirements.txt  Python packages (pip install -r)
#   course_data/      materials copied to /opt/workspace/<COURSE_NAME>/
#   base-version.txt  Learning Cloud release used as BASE_IMAGE
#
# The base already provides ROCm, ROCm PyTorch, JupyterHub singleuser,
# JupyterLab and the jovyan user (UID 1000, GID 100).
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Folder under /opt/workspace and the landing directory. build.sh / build.ps1
# set it to the course directory name.
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

# 3. Course materials.
COPY --chown=1000:100 course_data/ /opt/workspace/${COURSE_NAME}/

USER jovyan
WORKDIR /opt/workspace/${COURSE_NAME}
# Inherit the base image's JupyterHub single-user startup command.
