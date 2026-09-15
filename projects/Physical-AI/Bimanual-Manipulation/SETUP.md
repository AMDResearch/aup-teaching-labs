**中文** | [English](SETUP.en.md)

# 環境重建 — XLeRobot + SmolVLA（AMD GPU）

單臂版的通用步驟見 `~/so101_pick/SETUP.md`。這份只寫**這台雙臂機器的環境**:
硬體定址、LeRobot 本體要打的 patch、軟體套件的坑,以及實測查明的實體問題。

**流程與設計理由(為什麼分三段、為什麼 mask 閒置手、三個靜默失效模式)見 [README.md](README.md),這裡不重複。**

---

# 硬體

| 裝置 | 內容 | 定址方式 |
|---|---|---|
| `port1` | 左臂 6 顆 + 頭 2 顆（id 1-8） | by-id `usb-1a86_USB_Single_Serial_5B14034094-if00` |
| `port2` | 右臂 6 顆 + 輪子 3 顆（id 1-9） | by-id `usb-1a86_USB_Single_Serial_5B14115158-if00` |
| 左 / 右 leader | SO-101 leader | 錄製時才接,`/dev/ttyACM*` |
| head 相機 | USB Camera 640x480 | by-path `pci-0000:bf:00.4-usb-0:1:...`(獨立 controller) |
| wrist 相機 | USB Camera 640x480 | by-path `pci-0000:bd:00.4-usb-0:1.3:...` |

共 17 顆 Feetech STS3215，兩條 bus。

## follower bus 必須用 by-id，不能用 `ttyACM` 編號

cdc_acm 在列舉時取**最低的空號**，所以 `/dev/ttyACM0` 不是裝置的屬性。一個掉線
重連的轉接器會拿到當下任何空著的號碼 —— 實測這台機器在三分鐘內把兩隻手臂對調：
`3-1.2` 在 11:27 是 `ttyACM0`，11:30 變成 `ttyACM1`。

之後傳 `--port1=/dev/ttyACM0` 就是在對**另一隻手臂**下指令，而且**不會有任何錯誤**：
lerobot 的 handshake 只檢查「要的 id 在不在」，9 顆馬達的 bus 完全能通過「需要 8 顆」
的檢查。每一個關節指令都落在錯的馬達上。

`/dev/serial/by-id/` 對應轉接器自己的序號，換插槽也跟著走。所有腳本都改用它了。
換轉接器之後到該目錄下重新取得路徑（由馬達數量辨識：8 = 左臂+頭、9 = 右臂+輪）。

## 相機必須用 by-path，不能用 by-id

兩台相機是同型號，`ID_SERIAL` 字串完全一樣，所以 `/dev/v4l/by-id/` 只會生成
**一個** symlink，兩台指向同一個。單臂那套用 by-id 是因為那邊兩台型號不同。

`/dev/videoN` 更不能用：每次插拔都跳號（這台在一次 session 裡跳了六次以上）。
錄製和推論之間跳號會**靜默對調兩個視角**，資料就廢了。

```bash
ls -l /dev/v4l/by-path/*-usb-*-video-index0 | grep -v usbv2
```

`-video-index0` 是擷取節點，`-video-index1` 是 UVC metadata（開得起來但沒有畫面）。
筆電內建的 AMD ISP 會以 `platform-` 出現，也不是你的相機。

換孔位之後路徑會變,腳本裡的 `HEAD_CAM` / `WRIST_CAM` 要跟著更新。

---

# LeRobot 本體要打的三個 patch

XLeRobot 不在上游，只加 `src/lerobot/robots/xlerobot/` 是不夠的 —— 官方 CLI 讀的是
硬編碼清單。這三處都在**追蹤檔案**裡，`git pull` 會衝突。基準 commit `95211b98`。

**1. `src/lerobot/robots/utils.py`** — `make_robot_from_config` 加分支：

```python
elif config.type == "xlerobot":
    from .xlerobot import XLerobot

    return XLerobot(config)
```

**2. `src/lerobot/scripts/lerobot_rollout.py`** — 加進 import 清單以觸發
`@RobotConfig.register_subclass` 註冊，否則 `--robot.type=xlerobot` 是 invalid choice：

```python
from lerobot.robots import (  # noqa: F401
    ...
    xlerobot,
)
```

**3. `src/lerobot/rollout/context.py`** — 移除 `.pos` 過濾：

```python
observation_features_hw = {
    k: v for k, v in all_obs_features.items() if isinstance(v, tuple) or v is float
}
action_features_hw = dict(robot.action_features)
```

原本只保留 `.pos`，得到 14 維；但 `lerobot-record` 是用
`robot.observation_features` 全部 17 維建資料集的。維度不合，normalizer 會拋
`The size of tensor a (14) must match the size of tensor b (17)`。
上游程式碼裡的 `TODO(Steven)` 註解正好在講這個取捨。

## `use_head_wheels` 開關

`XLerobotConfig.use_head_wheels`（預設 `True`）。設成 `False` 時：

- `configure()` 只對 12 顆手臂馬達上 torque,頭和輪子保持無力
- `send_action()` 不寫入頭和輪子
- `stop_base()` 直接跳過

但**仍然照常讀取**它們,所以 `observation_features` 維持 17 維,
兩種模式錄出來的資料集互相相容。頭會因重力垂下,那是預期行為。

腳本用 `HEAD_WHEELS` 環境變數控制;直接下 CLI 的話是
`--robot.use_head_wheels=false`(draccus 會自動把 config 欄位變成參數)。

## `robots/xlerobot/xlerobot.py` 裡的三層容錯

這是自己的檔案，不會跟上游衝突：

- `MOTOR_RETRIES` — 每次 bus 讀寫的重試次數。馬達通電後偶發掉包，lerobot 預設
  只試一次，`lerobot-rollout` 一次掉包就中斷整個 run。故障是瞬間的，重試會成功。
- `get_camera_observation()` 的 last-frame fallback — 相機停滯時沿用上一張好影格
  並計數，而不是拋出。從未成功過的相機仍然會拋，不會靜默給空影格。
- `CAMERA_READ_TIMEOUT_MS` — 預設 200 ms 比一個 control tick 還長，一台相機遲到
  就會把整個迴圈拖到 5 Hz。設成接近單一 tick 的長度。

這三個都是**遮症狀**。根因見最後一節。

---

# 軟體環境

## 重建整個 conda env → `./build_env.sh`

```bash
./build_env.sh                    # 建成 lerobot,也就是各腳本 header 裡 conda activate 的那個
./build_env.sh --name foo         # 換個名字,不碰現有的
./build_env.sh --force            # 砍掉同名 env 重建
./build_env.sh --resume-from 2    # 某步失敗後,從第 N 步(1-5)接下去
./build_env.sh --dry-run          # 只印指令,什麼都不動
```

`environment.yml` 是 `conda env export` 的快照,**不能**直接 `conda env create -f` 餵進去:
ROCm wheel 不在 PyPI、`lerobot` 其實是 editable、opencv 會被蓋掉、還有幾個只有快照才看得到的
陷阱。`build_env.sh` 全部處理掉了,每一個的理由寫在腳本註解裡。同名 env 已存在時它會拒絕,
所以在已經有可用環境的機器上誤跑是安全的。

## opencv 必須換成 GUI 版

lerobot 依賴 `opencv-python-headless`,它沒編進 GUI,`cv2.imshow` 會丟
`error: (-2:Unspecified error) The function is not implemented.`。
`build_env.sh` 最後一步已經修掉了,但**每次跑過 `pip install -e .` 或 `uv sync` 都會再壞一次**
(兩個套件共用同一個 `cv2` 目錄,後裝的蓋掉前一個)。修法:

```bash
./build_env.sh --name <你的 env> --resume-from 5
```

## GPU（ROCm）

```bash
export TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1
```

`train_pipeline.sh` 和 `eval.sh` 已經內建。

## torchcodec 在這個環境載不起來 → 資料集必須是圖片型

`torchcodec 0.11.1` 是對上游 torch 建的,這台是 `2.12.0+rocm7.14.0`(AMD `rocm-sdk` wheel),
ABI 不合(補 `LD_LIBRARY_PATH` 也沒用)。影響:**影片型資料集錄得出來但讀不回來**,
訓練會在第一次取 frame 時死。

所以錄製一律帶 `--images-not-video`，影格以 PNG 內嵌在 parquet，完全繞過解碼器。
代價是磁碟：實測約 39 KB/張，兩台相機 30 fps ≈ 每集 200 MB，20 集約 4 GB。

## SmolVLA 需要 `--rename_map`，ACT 不需要

ACT 的 config 直接列 `head`/`wrist`，pre-flight 檢查會過。SmolVLA finetune 只改寫
output 的形狀，input 的名稱仍是從 `smolvla_base` 繼承的 `camera1/2/3`
(連 `observation.state: [6]` 也是過期的)。`build_rollout_context` 會拿 config 的
`input_features` 比對機器人提供的名稱，在碰到 policy 之前就擋下來:

```
ValueError: Visual feature mismatch ... Policy expects: camera1/2/3 ... Robot provides: head/wrist
```

推論本身其實吃 `head`/`wrist` 沒問題(normalizer 是用這份資料集建的)，但那個檢查看的是
config。`eval.sh` 和 `chain_eval.py` 都只對 SmolVLA 加這個 map。訓練時同理:
`--rename_map` 把 `head`/`wrist` 對到 `camera1`/`camera2`,`camera3` 空著就好
—— `smolvla_base` 預期三台相機,但驗證器接受子集,不用假造第三路。

## 為什麼 record 不用 `lerobot-record`

`lerobot-record` 需要一個 teleoperator config，但這台是**兩隻 leader 手臂 + 鍵盤
控頭和底盤**，沒有任何單一 teleop type 能表達。所以錄製走 `record_teleop.py`，
它自己建 `LeRobotDataset` 並用 `build_dataset_frame` 組 frame，feature 定義直接從
`robot.action_features` / `observation_features` 推導 —— 格式與 `lerobot-record` 一致。

同理 merge 由 `train_pipeline.sh` 自己做:`lerobot-train` 一次只吃一個資料集,
`MultiLeRobotDataset` 在這個版本會直接 `raise NotImplementedError`。

---

# 指令速查

完整流程見 [README.md](README.md)。這裡只列環境相關的注意事項。

## 看相機

```bash
python view_cams.py                          # 兩台，640x480
python view_cams.py --width 320 --height 240 # 走 SSH 時建議降解析度
python view_cams.py --cam wrist              # 只看一台
```

`ssh -X` 遠端看得到，實測 **12.1 Hz**（本機 29.9 Hz）。X11 forwarding 傳的是
未壓縮像素，1280×480 每張 1.8 MB，這是網路頻寬的上限不是機器的問題。

## Record

```bash
LEFT_LEADER=none RIGHT_LEADER=/dev/ttyACM2 ./record_phases.sh blue open,close
RIGHT_LEADER=none LEFT_LEADER=/dev/ttyACM2 ./record_phases.sh blue pick
./record_phases.sh --status        # 九份資料集的進度
python check_phases.py             # 品質檢查
```

| 鍵 | 作用 |
|---|---|
| `r` | 開始一輪 / 再按提前結束(保留已完成的 phase) |
| `c` | 結束這個 phase,開始下一個;最後一個之後整輪寫入 |
| `x` | 丟掉**整輪**(抽屜已經開了,中間那段沒辦法單獨重錄) |
| `w`/`s` `a`/`d` | 頭部俯仰 / 左右（需 `HEAD_WHEELS=1`） |
| `i`/`k` `j`/`l` | 底盤前後 / 旋轉（需 `HEAD_WHEELS=1`） |
| `n`/`m` | 底盤速度加減 |
| `ctrl-c` | 結束（錄到一半也會存起來，不會白錄） |

- **終端機必須保持 focus** —— 鍵盤是從 stdin 讀的（Wayland 下 pynput 抓不到全域按鍵）。
- `FPS` 在同一個資料集內必須一致 —— resume 檢查會比對 fps 並在不合時拒絕接續，
  而不是默默混進去。eval 時要用同一個值。
- 可重複執行接續，腳本自己判斷要不要 `--resume`。
- 結束時印的 `cycles: N, ... faults: X` 是這一輪的硬體故障統計，拿來比較不同接線方式很好用。

## Train

```bash
./train_pipeline.sh --prep-only   # 只做準備和驗證,不訓練
./train_pipeline.sh               # 準備 + 驗證 + 訓練
./train_pipeline.sh --train-only  # 跳過準備,直接訓練
./train_pipeline.sh --status      # 進度
```

準備階段 trim 成 12 維 → merge 三色 pick → 修正規化統計 → `verify_arms.py`，
**驗證不過就拒絕開始訓練**。全部 10 個模型約 12.5 小時,中途的 checkpoint 都能直接評估。

## Eval

```bash
./eval.sh blue 60             # 最新 checkpoint,藍色抽屜,60 秒
./eval.sh green 60 010000     # 指定 checkpoint
FPS=15 HORIZON=20 ./eval.sh blue
python chain_eval.py --stages act_open_blue,smolvla_pick_all,act_close_blue \
  --colour blue --checkpoint 005000,020000,005000 --trace /tmp/chain.npz
```

- **底盤安全**：policy 的輸出含 `x.vel`/`y.vel`/`theta.vel`。訓練時底盤沒動,
  所以那三維是接近零的**雜訊** —— 但不是零。預設 `HEAD_WHEELS=0` 會讓這些指令
  到不了輪子;如果改成 `1`,地面要淨空、手放在 ctrl-c 上。
- 用 `--inference.type=rtc` 而不是 `sync`：sync 每個 control tick 跑一次完整推論
  （約 320 ms），迴圈被壓到 3 Hz。RTC 在背景執行緒算下一個 chunk。
  `HORIZON` 要讓一個 chunk 的播放時間長於推論時間：10 個 tick @15fps = 0.67 s。
- 顏色決定 task 字串,**必須跟錄製時一字不差**。

---

# 疑難排解

| 症狀 | 原因 |
|---|---|
| `invalid choice: 'xlerobot'` | 上面的 patch 2 沒打 |
| `size of tensor a (14) must match b (17)` | patch 3 沒打 |
| `Visual feature mismatch ... camera1/2/3` | SmolVLA 少了 `--rename_map` |
| `imshow: The function is not implemented` | headless opencv 又被裝回來 |
| `Could not load libtorchcodec` | 資料集是影片型的，要用 `--images-not-video` 重錄 |
| `There is no status packet` | bus 掉包；先確認沒有第二個 process 開著同一個 port |
| `observation failed: (5, ...)` + `Port is in use!` | USB 轉接器重置,見下方「交棒電流尖峰」 |
| `VIDIOC_REQBUFS: errno=19` | 相機從 USB bus 上消失，實體問題 |
| `Corrupt JPEG data` | 兩台相機孔位太近,見下方 |
| loop 跑得比目標 fps 慢 | 調大 `HORIZON` 或降 `--fps` |
| 相機路徑不存在 | 換過孔位，重新 `ls /dev/v4l/by-path/` |

## 多開 process 是最難認的一個

Linux 的 serial port 沒有強制鎖。同時跑兩個 script，兩邊都會在同一條 bus 上
發指令、搶讀回應，症狀是 `There is no status packet` 和
`Incorrect status packet` 交替出現，看起來完全像硬體故障。

`record_phases.sh` 和 `eval.sh` 啟動時會用 `fuser` 檢查並拒絕啟動。手動確認：

```bash
fuser -v /dev/ttyACM0 /dev/ttyACM1     # 應該沒有輸出
```

## 交棒電流尖峰造成的 USB 重置,長得跟線材鬆脫一樣

成因與解法見 README「交棒的姿勢跳變」。這裡只記怎麼**分辨**:

```
[RUN] observation failed: (5, '輸入/輸出錯誤')
... Failed to sync read 'Present_Position' ... [TxRxResult] Port is in use!
```

`journalctl -k` 會看到真的 `USB disconnect` + 重新列舉。用 `lerobot-replay` 重播
`pick_blue_arms` 的 episode 0 —— 同樣的動作、同樣的 bus、沒有 policy;
replay 正常就不是硬體。

---

# 實體問題根因

長期以來的 `There is no status packet`、`Corrupt JPEG data`、USB 反覆重新列舉、
`/dev/ttyACM*` 跳號,**是兩個獨立的實體問題**,不是同一個。

## 0. 啟動前:手臂必須在校正範圍內

torque 在 `connect()` 之前是關的,所以手臂可以被手推到校正範圍**之外**。一旦通電,
伺服會把目標值夾回 `[Min_Position_Limit, Max_Position_Limit]` 並**驅動它一路回到範圍內** ——
在 shoulder_lift 這種抗重力關節上,那是一個幾百 counts 的強制移動,足以觸發馬達自己的
`Unloading_Condition` 過載保護:它會**卸載扭力並閃燈**,teleop 當場失效。

`record_teleop.py` 啟動時會檢查並列出超出範圍的關節。看到警告就用手把它撥回去。

實例:一次測試中 shoulder_lift 停在 3967、上限是 3244(超出 723),通電後 peak current
衝到 455、load 頂到 1000(其他關節只有 7-13),然後卸載閃燈。**馬達本身完全正常** ——
撥回範圍內重新上電,靜態保持 8 秒 load/current 都是 0。

順帶排除:leader 與 follower 的校正**沒有不一致**。五個手臂關節的 span 比值都在 1.00x
(shoulder_lift 是 2367 vs 2378),`drive_mode` 全部相同。只有 gripper 差 0.81x ——
同樣的正規化變化量,follower 夾爪會多走約 290 counts,也就是夾得比 leader 顯示的更緊。
那值得修,但與過載無關。

## 1. 相機 MJPG 損壞 — 兩台相機的 USB 孔位太近

兩台插在相鄰(或同一顆 hub 的鄰近)孔位時,MJPG 影格會大量損壞。libjpeg 多半能從
restart marker 重新同步,所以**不會拋例外**,只會在 stderr 印 `Corrupt JPEG data`,
壞掉的影格照樣被寫進資料集。

**把兩台分開到相隔較遠的孔位(不同 controller)就解決了。**

量測參考(當時兩台都在同一顆內建 hub 上):

| 條件 | 影格 | MJPG 損壞 |
|---|---|---|
| 只有相機,機器人沒連 | 1346 | 0.0% |
| 相機 + 機器人連上、torque 開 | 1116 | 34.0% |

當時我把這個差異歸因於馬達雜訊,那個推論是錯的 —— 真正的變因是孔位距離。
現在 head 在 `pci-0000:bf:00.4`、wrist 在 `pci-0000:bd:00.4`,分屬不同 controller。

## 2. 馬達 bus 斷線 — 右臂 shoulder_lift(id 2)故障

逐顆通電測試的結果非常乾脆:

```
id 1 shoulder_pan    moved 56/60   peak cur 11   healthy
id 2 shoulder_lift   ← 通電下命令的瞬間,USB 裝置直接消失
id 3-6               全部 Input/output error(port 已經不存在)
```

kernel log 同一秒:`usb 3-1.4: USB disconnect` → 一秒後重新列舉成新的 ttyACM 號碼。

**這顆馬達一被驅動就把電源軌拉垮**(先前量到 12.1 V → 5.0 V),USB 轉板 brownout 重開機。
所有的 serial 掉包、跳號、`termios.error` 都是這個的後果。

分辨燒毀或卡死:torque 關掉後用手轉 shoulder_lift。轉得順 = 繞組短路或驅動級燒毀,
換馬達;轉不動 = 機構卡死,先排除機構。

**沒排除之前收不了資料** —— 抓取動作一定會用到 shoulder_lift,每次驅動都讓整個
USB 子系統重啟。

## 排除掉的假設(都有數據)

| 假設 | 證據 |
|---|---|
| 內建 hub 本身不穩 | 靜態 5378 tick、29.9 Hz,零故障 |
| 某個暫存器有問題 | 五種暫存器各 sync_read 100 次,零失敗 |
| torque 通電就干擾 | 靜止保持 150 次讀取,torque 開/關都零失敗 |
| USB 頻寬不足 | 相機 fps 減半,損壞率不變(每秒次數固定,與流量無關) |
| 頭和輪子參與 | 靜態 A/B 兩組都零故障 |

---

# 檔案位置

| 什麼 | 在哪 |
|---|---|
| 分段資料集 | `~/lerobot_data/xlerobot_{open,pick,close}_{yellow,blue,green}/` |
| 訓練用(12 維) | 同上加 `_arms` 後綴,由 `trim_dims.py` 產生 |
| pick 合併集 | `~/lerobot_data/xlerobot_pick_all_arms/` |
| checkpoint | `~/lerobot_out/{act_*,smolvla_pick_all}/checkpoints/` |
| 訓練 log | `~/lerobot_out/run_<model>.log`、`train_pipeline.log` |
| 校正檔 | `~/.cache/huggingface/lerobot/calibration/robots/xlerobot/` |
| XLeRobot 驅動 | `<lerobot>/src/lerobot/robots/xlerobot/` |
| conda env | `~/miniforge3/envs/lerobot/`,快照在 `environment.yml`,重建用 `build_env.sh` |
