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

# Introduction to Artificial Intelligence Labs

Four hands-on notebooks introduce practical artificial intelligence through local language models, agents, computer vision, and image generation. Every implementation is contained in its notebook; no auxiliary Python source files are required.

## Lab Descriptions

### **Story Relay Robot**

- **Focus**: Interactive story continuation with a local language model
- **Key Learning**: REST APIs, streamed responses, prompt construction, and local model serving
- **Implementation**: The notebook sends OpenAI-compatible requests to LM Studio and renders generated story continuations as they arrive.
- **Notebook**: [`IAI01_Story_Relay_Robot.ipynb`](IAI01_Story_Relay_Robot.ipynb)

### **Minesweeper Agent**

- **Focus**: The Observe → Plan → Act → Reflect agent loop
- **Key Learning**: State observation, candidate actions, tool calls, feedback, safety boundaries, and human approval
- **Implementation**: A self-contained game engine and agent first revise a constrained Minesweeper specification, then use the resulting tool set to play through an interactive widget.
- **Notebook**: [`IAI02_Minesweeper_Agent.ipynb`](IAI02_Minesweeper_Agent.ipynb)

### **Fruit Ninja**

- **Focus**: Hands-free interaction driven by pose estimation
- **Key Learning**: Pose keypoints, ROCm inference, frame processing, collision detection, game state, and rendering
- **Implementation**: A local YOLOv8 pose model maps wrist keypoints to blades used to slice moving fruit. The first code cell downloads the checksum-pinned model and sprite assets into `runtime_assets/` when needed.
- **Notebook**: [`IAI03_Fruit_Ninja.ipynb`](IAI03_Fruit_Ninja.ipynb)

### **Art Director**

- **Focus**: Prompt expansion and comparison of diffusion generation strategies
- **Key Learning**: Local LLM prompting, deterministic seeds, multi-step diffusion, one-step generation, GPU memory ownership, and measured runtime comparison
- **Implementation**: LM Studio expands one intent; Stable Diffusion 1.5 and SD-Turbo receive the same prompt and seed. The first code cell downloads pinned model revisions into `runtime_assets/` and reuses existing snapshots.
- **Notebook**: [`IAI04_Art_Director.ipynb`](IAI04_Art_Director.ipynb)

## Requirements

- Docker with access to a supported AMD GPU and ROCm runtime for Fruit Ninja and Art Director
- A webcam for interactive Fruit Ninja play
- LM Studio running an OpenAI-compatible local server at `http://127.0.0.1:1234` for Story Relay Robot, Minesweeper Agent, and Art Director
- Several gigabytes of free disk space for downloaded pose and diffusion models
- Internet access on the first Fruit Ninja and Art Director run

Ryzen AI APU hosts, including Radeon 8060S (`gfx1151`), must use the Ubuntu 24.04 OEM 6.14 kernel required by the [AUP Learning Cloud prerequisites](https://github.com/AMDResearch/aup-learning-cloud#prerequisites). Install it and reboot before building or running the image:

```shell
sudo apt update
sudo apt install linux-oem-6.14
sudo reboot
uname -r  # expected: 6.14.x-oem
```

PyTorch exposes AMD HIP devices through CUDA-compatible API names. In these notebooks, `cuda:0` means the first ROCm device.

## Build the Environment

From this directory:

```shell
docker build -t aup-introduction-to-ai .
```

Select the base-image tag that matches the host GPU. For example, Radeon AI PRO R9700 (`gfx1201`) uses the `gfx120x` image:

```shell
docker build \
  --build-arg BASE_IMAGE=ghcr.io/amdresearch/auplc-base:latest-gfx120x \
  -t aup-introduction-to-ai .
```

The Dockerfile extends the shared AUP ROCm image and installs the dependency union for all four notebooks. Model weights and game sprites are intentionally not baked into the image; the relevant notebooks download them at runtime.

## Run the Labs

Start the container with GPU access, expose Jupyter, and mount a writable cache location if you want downloaded assets to survive container replacement. The exact GPU flags depend on the host ROCm installation. A typical Linux launch is:

```shell
docker run --rm -it \
  --device=/dev/kfd --device=/dev/dri \
  --group-add "$(stat -c '%g' /dev/kfd)" \
  -p 8888:8888 \
  -v "$(pwd)/runtime_assets:/opt/workspace/Introduction-to-Artificial-Intelligence/runtime_assets" \
  aup-introduction-to-ai \
  jupyter notebook --ip=0.0.0.0 --no-browser
```

The supplemental group uses the host `/dev/kfd` group ID, which may differ from the container's `video` or `render` group IDs. For webcam use, also pass the host camera device supported by your Docker platform. LM Studio runs on the host; configure its endpoint environment variable in a notebook if `127.0.0.1` is not reachable from the container.

Open the desired notebook and run its cells from top to bottom. Fruit Ninja and Art Director may take time on their first run while assets download. Later runs reuse valid local files.

## External Model Terms

The YOLO model is distributed by Ultralytics; review its applicable license and terms. Stable Diffusion 1.5 and SD-Turbo retain the licenses and acceptable-use requirements published with their model repositories. Review those terms before redistributing downloaded weights or generated content.
