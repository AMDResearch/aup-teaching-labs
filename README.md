# AUP Teaching Labs

To remove the barriers to teaching.

The AMD University Program (AUP) Teaching Solutions initiative empowers educators with high-quality, hands-on curriculum resources for modern AI and robotics, designed for AMD GPU acceleration. These labs run on [AUP Learning Cloud](https://github.com/AMDResearch/aup-learning-cloud) — a JupyterHub platform delivering ROCm-accelerated course environments.

> **Branches**
> - `main` — reimplementation-ready course code: Jupyter notebooks, assets, and the matching Dockerfile for each lab.
> - `doc` — the GitHub Pages web portal (`index.html`, `assets/`, per-lab pages). Browse it at [https://amdresearch.github.io/aup-teaching-labs/](https://amdresearch.github.io/aup-teaching-labs/).

## Repository Structure

```
aup-teaching-labs/
└── projects/
    ├── CV/                                  # Computer Vision notebooks
    ├── DL/                                  # Deep Learning notebooks
    ├── LLM/                                 # LLM from Scratch notebooks
    └── Physical-AI/
        ├── Physical-Simulation/
        │   ├── Genesis-Simulation/          # Genesis (formerly PhySim) + Dockerfile
        │   └── Mujoco-Simulation/
        │       ├── mujoco-torch/            # MuJoCo + PyTorch labs + Dockerfile
        │       └── mujoco-MJX/              # MuJoCo MJX labs + Dockerfile
        └── Real-Deployment/
            ├── Robot-Policy-Deployment/     # (planned)
            └── ROS2-Deployment/             # (planned)
```

Each lab folder contains its Jupyter notebooks, any required assets, and a `Dockerfile` describing the environment needed to run it.

## Course Taxonomy

### 1. Physical AI

#### 1-1 Physical Simulation

- **1-1-1 Genesis Simulation** (`projects/Physical-AI/Physical-Simulation/Genesis-Simulation/`)
  Robotics and physics simulation with [Genesis](https://github.com/Genesis-Embodied-AI/Genesis) on AMD GPUs — load robots into scenes, apply PD controllers, pick-and-place with Inverse Kinematics, and scale to parallel environments.

- **1-1-2 Mujoco Simulation**
  - **mujoco-torch** (`.../Mujoco-Simulation/mujoco-torch/`) — Robosuite/MuJoCo with PyTorch: controllers and cameras, Gymnasium and rewards, behavior cloning, SmolVLA fine-tuning, PPO, and cross-domain RL.
  - **mujoco-MJX** (`.../Mujoco-Simulation/mujoco-MJX/`) — MuJoCo MJX: MJCF concepts, rendering and contacts, control and IK, MuJoCo→MJX, parallel rollouts and domain randomization, and MuJoCo Playground PPO.

#### 1-2 Real Deployment

- **1-2-1 Robot Policy Deployment** — placeholder for upcoming content.
- **1-2-2 ROS2 Deployment** — placeholder for upcoming content.

## Other Labs

- **Computer Vision** (`projects/CV/`) — classification, detection, segmentation, tracking, and generative vision models in PyTorch.
- **Deep Learning** (`projects/DL/`) — classical ML through neural networks, CNNs, GANs, and Transformers from first principles.
- **Large Language Model from Scratch** (`projects/LLM/`) — PyTorch fundamentals to a working LLaMA-style decoder.

## Running on AUP Learning Cloud

These notebooks are designed to run on [AUP Learning Cloud](https://github.com/AMDResearch/aup-learning-cloud), AMD's JupyterHub platform for hands-on AI education, which provides pre-built course images with AMD GPU acceleration via ROCm. Each lab's `Dockerfile` mirrors the image used to run that lab.

Full platform documentation: [https://amdresearch.github.io/aup-learning-cloud/](https://amdresearch.github.io/aup-learning-cloud/)

## Acknowledgments

Lab content is adapted from [AMDResearch/aup-learning-cloud](https://github.com/AMDResearch/aup-learning-cloud), developed as part of the AMD University Program in collaboration with university partners including National Taiwan University (CV, DL) and Nanjing University (LLM).

## License

Lab notebooks retain the copyright and license terms from the original AUP Learning Cloud project. See individual notebook headers for details.
