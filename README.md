# AUP Teaching Labs

To remove the barriers to teaching.

The AMD University Program (AUP) Teaching Solutions initiative empowers educators with high-quality curriculum resources designed to support modern computing and AI education. AUP Teaching Labs provides hands-on Jupyter notebook labs for modern AI and robotics, designed for AMD GPU acceleration. These labs can be run directly on [AUP Learning Cloud](https://github.com/AMDResearch/aup-learning-cloud) — a JupyterHub platform that delivers course environments with ROCm-accelerated GPUs.

**Browse the labs:** [https://amdresearch.github.io/aup-teaching-labs/](https://amdresearch.github.io/aup-teaching-labs/)

## Overview

AUP Teaching Labs provides four progressive teaching labs that cover the major areas of modern AI. Each lab is a series of Jupyter notebooks — from classical machine learning and computer vision to large language models and physics simulation.

## Teaching Labs

| Lab | Notebooks | Description |
| --- | ---: | --- |
| [**Computer Vision**](projects/CV/) | 8 | Image classification, object detection, segmentation, tracking, and generative vision models in PyTorch |
| [**Deep Learning**](projects/DL/) | 12 | Classical ML through neural networks, CNNs, GANs, and Transformers from first principles |
| [**Large Language Model from Scratch**](projects/LLM/) | 14 | PyTorch fundamentals to a working LLaMA-style decoder — attention, training, inference, and LoRA |
| [**Physics Simulation**](projects/PhySim/) | 4 | Robotics and physics simulation with Genesis on AMD GPUs |

### Computer Vision (8 labs)

From image classification with CNNs and ResNets to object detection (YOLOv9), segmentation (SegNet, SAM), multi-object tracking, and generative models (VAE, Diffusion).

### Deep Learning (12 labs)

Build machine learning knowledge from first principles — PCA, SVM, K-Means, Decision Trees, Regression — then neural networks, Word2Vec, CNNs, autoencoders, GANs, and a Transformer from scratch.

### Large Language Model from Scratch (14 labs)

Go from tensors and gradients to a working LLaMA-style decoder. Covers tokenisation, attention, FlashAttention, MoE, LoRA, training pipelines, KV-Cache, and building a Tiny LLaMA from scratch.

### Physics Simulation (4 labs)

Get started with [Genesis](https://github.com/Genesis-Embodied-AI/Genesis) — load robots into simulated scenes, apply PD controllers, perform pick-and-place with Inverse Kinematics, and scale to parallel environments for reinforcement learning.

## Running on AUP Learning Cloud

These notebooks are designed to run on [AUP Learning Cloud](https://github.com/AMDResearch/aup-learning-cloud), AMD's JupyterHub platform for hands-on AI education. Learning Cloud provides pre-built course images with all dependencies installed and AMD GPU acceleration via ROCm.

1. Deploy AUP Learning Cloud following the [Quick Start guide](https://amdresearch.github.io/aup-learning-cloud/installation/quick-start.html)
2. Launch the course environment for the lab you want (CV, DL, LLM, or PhySim)
3. Open the notebooks from the `projects/` directory in JupyterLab

Full platform documentation: [https://amdresearch.github.io/aup-learning-cloud/](https://amdresearch.github.io/aup-learning-cloud/)


## Repository Structure

```
aup-teaching-labs/
├── index.html              # Lab portal (GitHub Pages)
├── assets/                 # Site styles and images
└── projects/
    ├── CV/                 # Computer Vision notebooks
    ├── DL/                 # Deep Learning notebooks
    ├── LLM/                # LLM from Scratch notebooks
    └── PhySim/             # Physics Simulation notebooks
```

Each project folder contains Jupyter notebooks (`.ipynb`) and an `index.html` overview page with lab descriptions and links.

## Acknowledgments

Lab content is adapted from [AMDResearch/aup-learning-cloud](https://github.com/AMDResearch/aup-learning-cloud), developed as part of the AMD University Program in collaboration with university partners including National Taiwan University (CV, DL) and Nanjing University (LLM).

## License

Lab notebooks retain the copyright and license terms from the original AUP Learning Cloud project. See individual notebook headers for details.
