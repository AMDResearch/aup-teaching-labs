# LeKiwi 自主導航 (ROS2 Jazzy + Nav2 + RTAB-Map)

**[English](README.en.md)** | 中文

用 **ZED 2i 立體相機 + LeKiwi 全向底盤**，在**純 AMD 機器**上做 SLAM 建圖與自主導航。
免 ZED SDK：抓原始 stereo → RAFT-Stereo 在 AMD GPU(ROCm) 算深度 → RTAB-Map 建圖/定位 → Nav2 導航。



> **`cd ~/lerobot/lekiwi/ros2`**


## Environment Setup

### 測試平台

| 項目 | 版本 |
|---|---|
| CPU / iGPU | AMD Ryzen AI 7 350 / Radeon 860M（**gfx1152**, RDNA3.5, UMA 共用 ~30GB RAM）|
| OS | Ubuntu 24.04 |
| Kernel | **6.14 OEM**（`linux-image-6.14.0-10xx-oem`；AMD Ryzen ROCm 要求 6.14-1018 OEM 或更新）|
| ROCm | **7.13.0**（preview；gfx1152 原生支援）|
| PyTorch | `torch 2.11.0+rocm7.13.0`（gfx1152 專屬 wheel，**原生、免 HSA override**）|
| ROS2 | Jazzy（`/opt/ros/jazzy`）+ Nav2 + rtabmap_ros |

### 0. 系統：AMD OEM 核心 + ROCm（Ryzen APU 官方流程）

```bash
# OEM 核心（gfx1152 需要 6.14-1018 OEM 或更新），裝完重開機並選這顆
sudo apt update && sudo apt install linux-image-6.14.0-1020-oem linux-headers-6.14.0-1020-oem
sudo apt install libatomic1 libquadmath0
# ROCm 7.13 apt 源（官方 gfx1152 支援）
sudo mkdir -p /etc/apt/keyrings
wget https://repo.amd.com/rocm/packages/gpg/rocm.gpg -O - | gpg --dearmor | sudo tee /etc/apt/keyrings/amdrocm.gpg >/dev/null
echo 'deb [arch=amd64 signed-by=/etc/apt/keyrings/amdrocm.gpg] https://repo.amd.com/rocm/packages/ubuntu2404 stable main' | sudo tee /etc/apt/sources.list.d/rocm.list
sudo apt update && sudo apt install amdrocm7.13-gfx1152      # gfx1152 專屬 meta 套件
sudo usermod -aG render,video,dialout $USER    # 重登生效(GPU + 馬達序列埠)
# 驗證: rocminfo | grep gfx1152 ; amd-smi version
```

### 1. Conda 環境 `lerobot-new`（ROCm PyTorch + lerobot + 深度依賴）

```bash
conda create -n lerobot-new python=3.12 -y
conda activate lerobot-new
# lerobot（含 Feetech 馬達支援）
cd ~/lerobot && pip install -e ".[feetech]"
# 覆蓋成 gfx1152 原生 ROCm PyTorch（ROCm 7.13 專屬 wheel index，免 HSA override）
pip install --index-url https://repo.amd.com/rocm/whl/gfx1152/ "torch==2.11.0+rocm7.13.0" "torchvision==0.26.0+rocm7.13.0"
# RAFT-Stereo 深度用到的
pip install scipy matplotlib opt_einsum scikit-image opencv-python pyyaml
```

### 2. ROS2 Jazzy + Nav2 + RTAB-Map

```bash
sudo apt install ros-jazzy-desktop ros-jazzy-navigation2 ros-jazzy-nav2-bringup ros-jazzy-rtabmap-ros v4l-utils
```
> conda 的 python 可直接 `import rclpy`（同為 cpython 3.12）—— 執行時加
> `export PYTHONPATH=/opt/ros/jazzy/lib/python3.12/site-packages:$PYTHONPATH`（demo 腳本已內建）。

### 3. 本 repo：RAFT 權重 + 相機校正

```bash
cd ~/lerobot/lekiwi/ros2
bash utils/setup_raft.sh                      # 下載 RAFT-Stereo + 權重(約 200MB)
bash utils/get_calibration.sh <你的ZED序號>    # 下載你這顆 ZED 的出廠校正 -> utils/calib.conf
```

### 硬體與注意

- 馬達 `/dev/ttyACM0`、ZED 在 `/dev/videoN`（索引每次開機會變，腳本自動偵測）
- ⚠️ **每次導航前把車放回「原點」** —— 也就是 **demo0 建圖錄製開始時，車所在的位置與朝向**。demo1/demo2 會自動把定位初始化在這個原點；位置或朝向不對，整張地圖座標會偏。建議在地板做個記號，每次放回同一起點同一朝向。


---

## 資料夾

| 資料夾 | 內容 |
|---|---|
| `utils/` | 共用執行期：底盤 driver、ZED live node、Nav2 設定/launch、RTAB-Map 定位、清理腳本、相機校正、**產出的地圖**、RAFT-Stereo(連結) |
| `demo0/` | 掃描建圖：鍵盤開車錄製 → 深度 → RTAB-Map → 2D 網格 → 積木定位 → 產出 `utils/` 裡的地圖（`build_map.sh`） |
| `demo0-v2/` | 同上但**機器人自主前沿探索**（不用鍵盤），探索完自動接 `demo0/build_map.sh` 離線高品質建圖 |
| `demo1/` | Nav2 最基本：RViz 點一個點，車開過去 |
| `demo2/` | 走到指定顏色積木（紅/紫/藍/綠） |

**產出物（demo0 產生，demo1/2 使用）**：`utils/scene_map.pgm` `utils/scene_map.yaml`（含積木障礙）、`utils/block_waypoints.txt`。

---


## Demo 0：建立地圖（掃一次場景）

**1) 錄製**（conda 環境，鍵盤開車 WASD，繞場景一圈**回到起點停下**）
```bash
cd ~/lerobot/lekiwi/ros2
source ~/miniconda3/etc/profile.d/conda.sh && conda activate lerobot-new
PYTHONNOUSERSITE=1 python demo0/lekiwi_map_gui.py --cam-dev 0     # 視窗:開始/停止錄製
```
→ 產生 `demo0/rec/mapYYYYmmdd_HHMMSS/`

**2) 一鍵建圖**（自動：深度→RTAB-Map→2D網格→積木定位→標障礙）
```bash
bash demo0/build_map.sh demo0/rec/map20260704_114742          # 換成你的 session
```
→ 產出 `utils/scene_map.*` + `utils/block_waypoints.txt`。約 10-15 分鐘。

**Demo0 example：**

<img src="imgs/example_map.png" width="360">

---

## Demo 0-v2：自主探索建圖（機器人自己走，不用鍵盤）

同樣的地圖，但**機器人自主前沿探索 (frontier exploration)**，不用人開車。線上跑 RTAB-Map 建圖 + Nav2，`auto_explore.py` 讀成長中的 `/map`，找「已知空地 ↔ 未知」的邊界當目標、自動派 Nav2 goal 把場景走完，最後**自動回原點**。全程同步錄製 raw stereo + odom，探索結束**自動接離線 `build_map.sh`（iters=32）**產出與 Demo 0 同級的漂亮地圖。

> 線上那張地圖只用來「決定往哪走」，是拋棄式的；最終地圖仍由離線高品質深度重建。慢速反而讓深度更清晰、迴圈閉合更準。

```bash
cd ~/lerobot/lekiwi/ros2
bash demo0-v2/demo0_autoexplore.sh                # 探索 + 每點掃描轉一圈 + 自動建圖
bash demo0-v2/demo0_autoexplore.sh --no-spin      # 不在每個點原地轉(較快)
bash demo0-v2/demo0_autoexplore.sh --no-build     # 只探索錄製,之後自己 build_map.sh
bash demo0-v2/demo0_autoexplore.sh --no-rviz      # 無頭(不開 RViz)
```
開始前一樣把車放到**地圖原點**（demo1/2 會用這個起點）。`Ctrl-C` 隨時安全中止 + 停馬達 + 清乾淨。約 15–30 分鐘（含離線建圖）。

---

## Demo 1：Nav2 點目標

```bash
cd ~/lerobot/lekiwi/ros2
bash demo1/demo1_click_nav.sh
```
起好後在 RViz：點工具列 **`Nav2 Goal`** → 在白色(可走)區點一個點 → 拖個方向 → 放開，車就開過去。
`Ctrl-C` 自動全部清乾淨。

**Demo1 example：**

[![demo1](imgs/demo1_poster.jpg)](imgs/demo1_example.mp4)

---

## Demo 2：走到指定顏色

```bash
cd ~/lerobot/lekiwi/ros2
bash demo2/demo2_color_nav.sh blue            # 走到藍色
bash demo2/demo2_color_nav.sh green red        # 綠→紅 依序
bash demo2/demo2_color_nav.sh red --standoff 0.05   # 調停靠距離(離積木多遠停,預設0.1m)
```
車會停在積木前方、大致面對它。`Ctrl-C` 自動清乾淨。

**Demo2 example：**

[![demo2](imgs/demo2_poster.jpg)](imgs/demo2_example.mp4)


---

## Cleanup

任何時候一鍵停掉所有節點 + 停馬達：
```bash
bash utils/lekiwi_cleanup.sh
```

---

## 已知限制 / 筆記

- **VGA 解析度**（ZED 走 USB2；接 USB3 藍孔可到 720p/1080p，特徵更多、迴圈閉合更準）。
- 輪速里程計是速度積分，長程/多旋轉後會漂；VGA 視覺定位修正有限 → **短程近目標較可靠**。
- gfx1152 **原生支援**（ROCm 7.13 + 對應 PyTorch，免 HSA override）；RAFT 首次執行約 80s 編 kernel、之後 ~1-2s/幀。
- 積木 ~5cm 高低於障礙偵測帶，靠 `mark_blocks.py` 補標成障礙才不會被輾過。
- 系統：AMD OEM 6.14 kernel + ROCm 7.13.0。
