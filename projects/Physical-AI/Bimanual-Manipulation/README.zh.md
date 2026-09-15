<!-- Copyright (C) 2025 Advanced Micro Devices, Inc. All rights reserved. -->

**中文** | [English](README.md)

# XLeRobot — cube to drawer, as a chain of per-arm policies

用 [LeRobot](https://github.com/huggingface/lerobot) 在 XLeRobot 雙臂上完成
「開抽屜 → 把紅色 cube 放進去 → 關抽屜」,拆成三段、各自訓練、在機器上串起來跑。

跑在 **AMD GPU(Radeon 8060S / gfx1151)** 上。

> **這個 repo 不能單獨運作。** 腳本 import 的 XLeRobot 驅動不在上游 lerobot 裡,先參考 [`lerobot_patches/`](lerobot_patches/),
> 照那裡的說明裝進你的 lerobot checkout。基準是 commit `95211b98`。
> 環境重建與所有踩過的坑見 [SETUP.md](SETUP.md)。

---

## 架構:
整個任務由**兩隻手**接力完成,而兩隻手在同一時間只有一隻在動:

```
右手 open  →  左手 pick  →  右手 close
```

### 分段錄製的關鍵決定

`open` 和 `close` **錄在同一個連續示範裡**,中間按 `c` 切換 —— 不是分兩次錄。
因為 close 的資料必須從 open 結束的那個狀態接續,那才是 run time 時
open policy 真正交棒的狀態。

### 閒置手的處理

每份錄製都含**兩隻手**(只接一支 leader,另一隻手固定不動)。所以 12 維的 policy
會把閒置手的姿勢當成常數讀進去、也當成常數吐出來 —— 兩者都不是決策。

`chain_eval.py` 因此對每一段做 **masking**:閒置手的輸入用它在訓練時的固定姿勢頂替,
輸出丟棄、原地保持。這等同於把資料切成單臂 6 維來訓練會得到的效果,
但不需要讓 robot 改成 advertise 6 channel。

---

## 目前的 pipeline

```bash
./setup_lerobot.sh                          # 準備 lerobot(驅動 + 四個 patch)
docker build -t auplc-bimanual .
./run.sh --check                            # 八項環境檢查

./run.sh ./record_phases.sh blue open,close # 右 leader:一次錄完 open + close
RIGHT_LEADER=none LEFT_LEADER=... \
  ./run.sh ./record_phases.sh blue pick     # 換左 leader:錄 pick
./run.sh python check_phases.py             # 資料品質
./run.sh ./train_pipeline.sh                # 準備 + 驗證 + 訓練 10 個模型
./run.sh python chain_eval.py --stages act_open_blue,smolvla_pick_all,act_close_blue \
  --colour blue --checkpoint 005000,020000,005000
```

### 環境:三步,之後全部走 `run.sh`

這個 lab 在 `aup-teaching-labs` 裡;lerobot 是另一個 clone,放在 repo 外面,執行時掛進容器。
分開的原因是 XLeRobot 不在上游 —— 那份 checkout 必須被改過(驅動 + 四個 patch),
放在外面你才讀得到、也改得動實際在跑的 lerobot 原始碼。

```bash
# 1. 這個 repo
git clone https://github.com/amd-weiliao/aup-teaching-labs.git ~/aup-teaching-labs
cd ~/aup-teaching-labs/projects/Physical-AI/Bimanual-Manipulation

# 2. 準備 lerobot(clone 到 ~/lerobot、放驅動、套四個 patch。可重複執行)
./setup_lerobot.sh
./setup_lerobot.sh --check      # 只報告狀態,不動任何東西

# 3. 建 image
docker build -t auplc-bimanual .
```

之後錄製、訓練、評估都在同一個容器:

```bash
./run.sh --check                             # 先驗環境,八項
./run.sh ./record_phases.sh blue open,close  # 錄製
./run.sh ./train_pipeline.sh                 # 訓練
./run.sh python chain_eval.py --stages ... --colour blue   # 評估
./run.sh                                     # 互動 shell
```

image 裡**沒有 lab 程式碼也沒有 lerobot 原始碼**,只有依賴。兩個目錄在執行時掛進去:

| 主機 | 容器 |
|---|---|
| 這個 lab 目錄 | `/workspace/lab`(工作目錄) |
| `~/lerobot`(可用 `LEROBOT=` 改) | `/workspace/lerobot` |
| `~/lerobot_data`、`~/lerobot_out` | `/home/jovyan/` 下同名 |

`PYTHONPATH=/workspace/lerobot/src` 讓掛進來的原始碼蓋過 image 裡安裝的那份,所以跑的是你打過
patch 的程式碼,而且兩個 repo 在主機改完都馬上生效,不用重 build。

### 為什麼需要 `run.sh`

flag 很多,而且每一項漏掉都會用不同方式安靜地失敗:

| flag | 少了會怎樣 |
|---|---|
| `--group-add render` | `/dev/kfd` 和 `/dev/dri/renderD*` 屬於 `render` 群組。少了它 `torch.cuda.is_available()` 是 `False`,沒有任何其他症狀 |
| `--group-add dialout` | `/dev/ttyACM*` 屬於 `dialout`,序列埠開不了 |
| `-v /dev/serial` `-v /dev/v4l` | `--device` 只傳字元裝置,不帶 by-id / by-path symlink —— 而所有腳本都用那些穩定名稱定址,不用 `ttyACM0` |
| `-e DISPLAY` `-v /tmp/.X11-unix` | `cv2.imshow` 開不出視窗(Wayland 下走 XWayland,所以 `DISPLAY` 是 `:0`) |

裝置只在存在時才傳,所以機器沒插也能進容器訓練。

### 三個實測出來的細節

**容器解掉了主機的 `dialout` 問題。** 容器的補充群組由 docker daemon(root)設定,不需要主機
使用者本身屬於該群組 —— 即使主機的你開不了 `/dev/ttyACM0`,容器裡是 RW 的。要修主機端:
`sudo usermod -aG dialout $USER` 再重新登入。

**權限不用處理。** 主機 uid 是 1000、容器 `jovyan` 也是 1000,掛進去的資料集讀得到、
寫回的 checkpoint 主機直接能用。

**`--images-not-video` 仍然必要。** base image 的 torch 是 2.9.1,而 torchcodec 0.11.1
需要 ≥2.11,所以容器讀不了影片型資料集。

### 資料

| | 每份 episode 數 | 驅動手 | 說明 |
|---|---|---|---|
| `open_{yellow,blue,green}` | 10 | 右 | 抽屜固定,軌跡幾乎一樣,不需要多 |
| `close_{yellow,blue,green}` | 10 | 右 | 與 open 錄在同一個 cycle |
| `pick_{yellow,blue,green}` | 18 | 左 | cube 在九宮格,每格 2 次 |
| `pick_all` | 54 | 左 | 三色合併,保留 3 個 task 字串 |

錄的是 17 維(含頭 2 + 底盤速度 3),訓練前用 `trim_dims.py` 切成 12 維雙臂。

### 模型

| | 架構 | 步數 | 說明 |
|---|---|---|---|
| `act_{open,close,pick}_{yellow,blue,green}` | ACT | 5000 | 9 個,每 1000 存一次 |
| `smolvla_pick_all` | SmolVLA | 20000 | 語言條件,每 5000 存一次 |

`act_pick_*` 和 `smolvla_pick_all` 是**同一段的兩種做法**,可以直接對照:
三個專才各看 18 筆單色,對上一個泛化模型看 54 筆但要讀指令。

---

## 檔案

### 環境與執行

| 檔案 | 用途 |
|---|---|
| `setup_lerobot.sh` | 準備 lerobot checkout:clone 到釘住的 commit、放 XLeRobot 驅動、套四個 patch(`--check` 只報告狀態) |
| `Dockerfile` | 容器環境。不含 lab 程式碼也不含 lerobot 原始碼,兩者執行時掛進來 |
| `run.sh` | 用正確的 GPU / 序列埠 / 相機 / X11 flag 啟動容器(`--check` 先驗環境) |
| `verify_env.py` | 八項環境檢查:torch 是不是 ROCm 版、GPU 真的算得動、依賴與原始碼一致、patch 有沒有生效。由 `train_pipeline.sh` 和 `docker build` 自動呼叫 |
| `environment.yml` · `build_env.sh` | 直接在主機用 conda 建環境的舊路徑,不走容器時才需要(見 SETUP.md) |

### 錄製

| 檔案 | 用途 |
|---|---|
| `record_phases.sh` | 分段錄製,一個連續示範寫進多個 dataset(`r` 開始、`c` 切段、`x` 丟棄整輪) |
| `record_teleop.py` | teleop + 錄製引擎(雙 leader + 鍵盤控頭/底盤,OpenCV 即時畫面) |
| `stdin_keyboard.py` | Wayland 下從 stdin 讀鍵盤,取代失效的 pynput |
| `check_phases.py` | 分段資料的品質檢查:episode 數、維度、恆定 channel、相機 |
| `delete_episodes.py` | 刪除 episode 並重新編號 |

### 訓練

| 檔案 | 用途 |
|---|---|
| `run.sh` | 用正確的 GPU / 序列埠 / 相機 / X11 flag 啟動容器(`--check` 先驗環境) |
| `verify_env.py` | 環境檢查:torch 是不是 ROCm 版、GPU 真的算得動、patch 有沒有生效(由 `train_pipeline.sh` 和 `docker build` 自動呼叫) |
| `train_pipeline.sh` | **主要入口**:trim → merge → 修 stats → 驗證 → 訓練 10 個模型 |
| `trim_dims.py` | 17 → 12 維(或 `--keep left/right` 切成單臂 6 維) |
| `fix_constant_stats.py` | 修正恆定 channel 的正規化統計(**沒有這步訓練會靜默失敗**) |
| `verify_arms.py` | 訓練前驗證資料,不過就拒絕開始(由 `train_pipeline.sh` 直接呼叫) |

### 評估

| 檔案 | 用途 |
|---|---|
| `chain_eval.py` | **多段串接**:連線一次、只換 policy、自動交棒、masking、ramp transition |
| `eval.sh` | 單段評估,走 `lerobot-rollout`(和官方路徑一致) |

### 相機

| 檔案 | 用途 |
|---|---|
| `view_cams.py` | 雙相機獨立預覽(確認視角、對焦、光線) |

### 裝進 lerobot 本體

| 檔案 | 用途 |
|---|---|
| `lerobot_patches/xlerobot/` | XLeRobot 驅動(雙 SO-101 + 頭 + 三輪底盤),放進 `src/lerobot/robots/` |
| `lerobot_patches/*.patch` | 四個 patch:讓官方 CLI 認得這台機器,並統一 record 與 rollout 的 state 維度 |

---

## `chain_eval.py` 做了什麼

每次交棒:

1. **ramp transition** — 走到下一段的示範起始姿勢,時間依距離決定(20 units/s)。
   只移動該段要驅動的關節。
2. **masking** — 閒置手的輸入頂替成訓練常數,輸出丟棄、原地保持。
3. **重置** — 新的 policy 不會帶著上一段的 action chunk。

```
ENTER  開始      c  提前結束這一段      h  凍結(原地不動,再按恢復)      x  中止
```

自動接續預設開啟,**用 tick 計算而不是秒**:chunked policy 的軌跡是每次呼叫前進一格,而 ACT 約 34ms(15Hz)、SmolVLA 約 320ms(3Hz)。用秒數計時會讓 SmolVLA 那段在走完四分之一時被切掉,看起來像模型失敗,其實是計時器先到。

| 選項 | 預設 | 說明 |
|---|---|---|
| `--colour` | 由模型名推得 | 語言條件模型必填(`smolvla_pick_all` 名字裡沒有顏色) |
| `--checkpoint` | `last` | 單一值或每段一個(`005000,020000,005000`) |
| `--auto` | `demo` | 由示範長度決定 tick 預算;或給秒數;`off` 改成手動 |
| `--ramp-speed` | 20 | 交棒ramp的速度,`0` 關閉(然後預期電流尖峰) |
| `--n-action-steps` | 0 | 沿用 checkpoint 原值(ACT 是 100)。**不要調成 1** —— 每 tick 重新決策會讓分布外的觀測自我維持,手臂原地卡死 |
| `--max-step` | 0 | 每 tick 位移上限。**預設關閉** —— Feetech 力矩正比於 (goal − present),限位移等於限力矩,一碰到抽屜阻力就拉不動 |
| `--trace` | — | 逐 tick 記錄成 `.npz`(commanded / observed / timestamp) |

---

## Issues

### 1. 為什麼拆成三段、兩種架構
SmolVLA對於長序列任務的表現較差，若把`右手 open  →  左手 pick  →  右手 close`完整動作做為一組data sample去訓練，兩隻手的移動容易互相干擾。因此拆分成三個小任務，open/close時櫃子固定手臂移動軌跡也較一致，因此使用ACT來訓練;pick時目標cube可能會在不同位置出現，需要較高的泛化性，因此使用SmolVLA訓練。

### 2. 恆定 channel 毀掉 MEAN_STD 正規化

只有一隻手被遙操作,另一隻手的 channel 完全不動。`(x − mean) / (std + 1e-8)` 的分子
是 float32 捨入殘差,除以 1e-8 之後變成 **10 到 100** 量級的常數目標。

訓練看起來完全正常 —— loss 從 0.68 平順降到 0.131,和一個能用的模型一樣好。
但整個 run 最大的梯度訊號是那個沒有意義的常數,訓出來的模型不看輸入:
在機器上 104 個 tick 只命令了 **0.2** 單位的位移,而示範是 **344**。

`fix_constant_stats.py` 把這些 channel 的 std 設成 1.0(判準是**原始變化範圍** < 1 單位,
不是 std —— 有個關節 std 0.0127 剛好躲過 std 門檻,但實際只動了 0.18 單位)。
修完 mean|z| 從 1.67 降到 0.44,同樣的架構 1000 步就能用。

`verify_arms.py` 會擋下沒修的資料集。

### 3. 交棒的姿勢跳變

見上方「分段錄製的關鍵決定」。48.7 單位的階躍指令 → 電流尖峰 → USB 轉接器重置 → EIO。
症狀和線材鬆脫完全一樣。`chain_eval.py` 的ramp + masking 解決。
