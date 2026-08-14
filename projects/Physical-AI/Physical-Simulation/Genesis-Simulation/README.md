# Genesis Simulation Labs

This directory contains six Jupyter labs for learning Genesis physical
simulation on AMD GPUs:

1. `PhySim01_hello_genesis.ipynb` — create a scene and load a robot
2. `PhySim02_control_your_robot.ipynb` — joint and PD control
3. `PhySim03_motion_planning.ipynb` — IK, motion planning, and grasping
4. `PhySim04_parallel_simulation.ipynb` — parallel GPU environments
5. `PhySim05_perception_with_rocm.ipynb` — ROCm vision and tactile perception
6. `PhySim06_language_guided_agent.ipynb` — tactile-gated, language-guided Physical AI agent

The Docker image uses `genesis-world==1.3.1` and includes the ROCm runtime,
PyTorch, JupyterLab, the Franka Panda assets, and all notebook dependencies.

## Requirements

- Linux with an AMD GPU supported by ROCm
- A working ROCm host driver
- Docker
- The following GPU device files:

```bash
ls -l /dev/kfd /dev/dri
```

The command should show `/dev/kfd` and at least one render device under
`/dev/dri`.

## 1. Build the image

Run these commands from this directory:

```bash
cd /path/to/aup-teaching-labs/projects/Physical-AI/Physical-Simulation/Genesis-Simulation

docker build -t auplc-physisim:physicalai-genesis .
```

The first build may take several minutes and use significant disk space because
Genesis and its rendering dependencies are included.

## 2. Start JupyterLab

```bash
docker run --rm -it \
  --name physisim-jupyter \
  --device=/dev/kfd \
  --device=/dev/dri \
  --group-add video \
  --ipc=host \
  --security-opt seccomp=unconfined \
  -p 127.0.0.1:8888:8888 \
  -v "$PWD:/opt/workspace/PhySim" \
  --entrypoint python3 \
  auplc-physisim:physicalai-genesis \
  -m jupyterlab \
  --ip=0.0.0.0 \
  --port=8888 \
  --no-browser \
  --ServerApp.token='' \
  --ServerApp.password=''
```

Open the following URL:

<http://localhost:8888/lab>

The current directory is mounted at `/opt/workspace/PhySim`, so notebook edits
and generated videos remain on the host after the container stops.

> The image's default `/entrypoint.sh` is intended for AUP Learning Cloud's
> JupyterHub deployment. Standalone Docker use must override it with
> `--entrypoint python3` as shown above.

## 3. Run the labs

Open the notebooks in numerical order, starting with PhySim01. Run cells from
top to bottom.

Genesis scenes should be built only once in a notebook kernel. If you need to
rerun a scene-creation cell, select **Kernel → Restart Kernel and Run All
Cells**.

Generated files are written to:

- `Videos/video_05.mp4` — raw tactile-grasp camera recording
- `Videos/video_05_hud.mp4` — vision, tactile, and GPU telemetry HUD
- `Videos/video_05_live_hud.mp4` — exported PhySim05 widget interaction
- `Videos/video_06.mp4` — raw language-guided mission recording
- `Videos/video_06_hud.mp4` — AI plan, execution, telemetry, and tactile HUD
- `Videos/video_06_live_hud.mp4` — exported PhySim06 widget interaction
- `Videos/interactive_session.mp4` — optional PhySim06 typed-command HUD
- `Artifacts/` — perception figures

The reusable OpenCV compositor, ffmpeg writer, and AMD GPU telemetry reader live
in `helpers/physisim_hud.py`. They consume notebook-owned NumPy arrays and
structured receipts; they do not build Genesis scenes, control the robot, or
start an LLM server.

### PhySim05 live simulation

After running PhySim05 through the **Live simulation interface** section, use
the notebook buttons in this order:

1. **Reset**
2. **Approach**
3. **Close to Secure**
4. **Lift**
5. **Lower**
6. **Release**

The widget updates the Genesis scene, GPU telemetry, six vision outputs, and
both 8×8 tactile heatmaps after each group of simulation steps. The contact
threshold and secure-taxel count can be adjusted with sliders. **Export HUD
MP4** writes the captured interaction to `Videos/video_05_live_hud.mp4`; the
regular top-to-bottom path keeps its own `video_05_hud.mp4`. Press **Shutdown
HUD** when finished to stop the telemetry thread.

Before pressing **Reset**, choose either the reproducible **Default** cube
layout or **Random (seeded)**. A seeded layout samples collision-free cube
positions inside the reachable workspace; using the same seed reproduces the
same positions. Reset also returns the Franka to its saved upright pose.

### PhySim06 live language-agent interface

After running PhySim06 through the **Live language-agent interface** section:

1. Enter a command such as `pick green` or `stack blue on red`.
2. Select the **Offline** or **LLM endpoint** planner.
3. Press **Run Command**.
4. Watch the validated plan, execution stages, scene, GPU telemetry, and tactile
   gate update in one AI BRAIN HUD.

The interface also provides **Home**, **Stop**, **Reset Scene**, and **Shutdown
HUD** buttons.
Its tactile sliders change the actual secure-grasp decision used by
`close_gripper()`. **Export HUD MP4** writes captured live commands to
`Videos/video_06_live_hud.mp4` without overwriting the Run All mission video.
**Reset Scene** applies the selected Default or seeded-random cube layout and
returns the Franka to its saved upright pose.

### Start the optional LLM endpoint

Offline mode works without any model. LLM mode requires a llama.cpp
`llama-server` binary and a GGUF model inside the same JupyterHub container.
The Docker image already contains a pinned Vulkan `llama-server` at
`/opt/llama/bin/llama-server`.

In PhySim06, press **Download Llama 3.2 3B GGUF (2.02 GB)**. The opt-in cell
downloads a pinned model from `bartowski/Llama-3.2-3B-Instruct-GGUF`, verifies
its SHA256, and stores it at:

```text
/opt/workspace/PhySim/models/Llama-3.2-3B-Instruct-Q4_K_M.gguf
```

The workspace mount keeps the model after the container stops. `models/` and
`*.gguf` are excluded from Git and Docker build context.

Llama 3.2 weights are subject to the
[Meta Llama 3.2 Community License](https://www.llama.com/llama3_2/license/),
not this course's MIT license. Review the applicable terms before downloading
or using the model.

After the download completes, open **JupyterLab → File → New → Terminal**:

```bash
bash helpers/start_llama_server.sh
```

Keep the Terminal open and verify the endpoint from another Terminal:

```bash
curl http://127.0.0.1:8081/health
```

Rerun PhySim06's LLM health-check cell, then select **LLM endpoint**. Press
`Ctrl+C` in the server Terminal to stop it. Advanced users may override
`LLAMA_SERVER_BIN` or `LLAMA_MODEL_PATH` before running the helper.

### Optional typed-command demo

After running PhySim06 through its function-definition cells, call:

```python
run_typed_demo(mode="offline")
```

Try `pick green`, `stack blue on red`, `home`, or `stop`. Enter `quit` to
finish and save `Videos/interactive_session.mp4`. The function is not called by
default, so automated notebook execution does not block on keyboard input.

## 4. Stop the container

Press `Ctrl+C` in the terminal running Docker, or run:

```bash
docker stop physisim-jupyter
```

The container is removed automatically because it was started with `--rm`.

## Automated notebook test

The following example executes PhySim05 non-interactively. A successful run
exits with status code `0`.

```bash
docker run --rm \
  --device=/dev/kfd \
  --device=/dev/dri \
  --group-add video \
  --ipc=host \
  --security-opt seccomp=unconfined \
  -v "$PWD:/opt/workspace/PhySim" \
  --entrypoint jupyter \
  auplc-physisim:physicalai-genesis \
  nbconvert \
  --to notebook \
  --execute \
  --ExecutePreprocessor.timeout=1200 \
  --output /tmp/PhySim05-executed.ipynb \
  /opt/workspace/PhySim/PhySim05_perception_with_rocm.ipynb
```

Run the same command for PhySim06 with a longer timeout:

```bash
docker run --rm \
  --device=/dev/kfd \
  --device=/dev/dri \
  --group-add video \
  --ipc=host \
  --security-opt seccomp=unconfined \
  -v "$PWD:/opt/workspace/PhySim" \
  --entrypoint jupyter \
  auplc-physisim:physicalai-genesis \
  nbconvert \
  --to notebook \
  --execute \
  --ExecutePreprocessor.timeout=2400 \
  --output /tmp/PhySim06-executed.ipynb \
  /opt/workspace/PhySim/PhySim06_language_guided_agent.ipynb
```

## Third-party assets and licenses

- Course notebooks and helper code: MIT
- Franka Emika Panda MJCF/assets: Apache-2.0; see
  `xml/franka_emika_panda/LICENSE`
- Llama 3.2 model weights: Meta Llama 3.2 Community License

## Troubleshooting

### `ModuleNotFoundError: No module named 'huggingface_hub'`

The running JupyterHub container predates the latest course image. Rerun the
PhySim06 download cell: it installs `huggingface-hub` into the current kernel
when missing. Alternatively, use a Terminal:

```bash
python -m pip install --user "huggingface-hub>=0.34,<2"
```

Then rerun the model-download cell. Newly built course images already include
the package.

### `Missing required environment $JUPYTERHUB_SERVICE_URL`

The image was started with its JupyterHub entrypoint. Use the standalone
JupyterLab command above, including `--entrypoint python3`.

### PyTorch reports that no GPU is available

Confirm the host devices exist and that both are passed to Docker:

```bash
ls -l /dev/kfd /dev/dri

docker run --rm \
  --device=/dev/kfd \
  --device=/dev/dri \
  --group-add video \
  --entrypoint python3 \
  auplc-physisim:physicalai-genesis \
  -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'No GPU')"
```

### Port 8888 is already in use

Map another host port, for example:

```bash
-p 127.0.0.1:8899:8888
```

Then open <http://localhost:8899/lab>.

### Accessing a remote machine

The launch command binds JupyterLab to localhost for safety. Forward the port
over SSH:

```bash
ssh -L 8888:localhost:8888 user@remote-host
```

Then open <http://localhost:8888/lab> on your local computer.

## Security note

The example disables the Jupyter token and password, but publishes the port
only on `127.0.0.1`. Do not expose this token-free server directly to an
untrusted network.

`--ipc=host` and `--security-opt seccomp=unconfined` reduce container isolation
and are used here for ROCm/Jupyter compatibility on the lab machine. Do not
copy these settings into a multi-tenant or production deployment without a
separate security review.

## Workspace permissions and GPU sharing

The bind mount replaces the notebooks, helpers, XML assets, and model directory
that were copied into the image. The host course directory must be writable by
the container's `jovyan` user (UID 1000), otherwise model downloads and videos
will fail:

```bash
sudo chown -R 1000:1000 /path/to/Genesis-Simulation
```

Genesis uses the AMDGPU/ROCm backend while the bundled llama-server uses
Vulkan/RADV. They share the same physical GPU and VRAM. If either process runs
out of memory, reduce `LLAMA_GPU_LAYERS`, stop other GPU workloads, or run the
LLM and simulation separately.
