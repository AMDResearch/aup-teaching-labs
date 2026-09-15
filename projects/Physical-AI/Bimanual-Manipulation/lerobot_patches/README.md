# 這些東西要放進 lerobot 本體

這個資料夾裡的腳本 import `lerobot.robots.xlerobot`,而那個驅動不在上游 lerobot 裡;
官方 CLI 也不認識這台機器。clone 下來之後要先做這兩件事,否則什麼都跑不起來。

基準 commit:`95211b98`(`feat(config): add multiprocessing option to DataLoader context`)。
上游改動之後 patch 可能對不上,那時看下面每個 patch 在做什麼,手動改比較快 —— 每個都只有幾行。

```bash
LEROBOT=~/lerobot        # 你的 lerobot checkout

# 1. XLeRobot 驅動(雙 SO-101 + 頭 + 三輪底盤)
cp -r xlerobot "$LEROBOT/src/lerobot/robots/"

# 2. 四個 patch
cd "$LEROBOT"
for p in <這裡>/lerobot_patches/*.patch; do git apply "$p"; done
```

## 每個 patch 在做什麼

| patch | 檔案 | 內容 |
|---|---|---|
| `utils.patch` | `robots/utils.py` | `make_robot_from_config` 加上 `xlerobot` 分支 |
| `lerobot_rollout.patch` | `scripts/lerobot_rollout.py` | import 清單加 `xlerobot`,draccus 才會註冊這個 choice |
| `lerobot_replay.patch` | `scripts/lerobot_replay.py` | 同上 |
| `context.patch` | `rollout/context.py` | 拿掉只留 `.pos` 的過濾器 |

前三個是同一件事的三個入口:`RobotConfig.register_subclass("xlerobot")` 只在
模組被 import 時執行,而那些 CLI 的 import 清單是寫死的,所以不改就等於這台機器不存在。

第四個比較不明顯。`lerobot-record` 用 `robot.observation_features` **全部**建資料集,
而 XLeRobot 回報 12 個手臂 + 2 個頭的 `.pos` 加上 3 個底盤 `.vel`,共 17 維。
但 rollout 的 `build_rollout_context` 只保留 `.pos`,得到 14 維 —— 於是錄的是 17 維、
跑的是 14 維,normalizer 直接因維度不合而拒絕。拿掉過濾器讓兩邊一致。

## 相依套件

`lerobot` 指定 `opencv-python-headless`,但這裡要用 `imshow` 顯示即時畫面:

```bash
pip uninstall -y opencv-python opencv-python-headless
pip install opencv-python==4.13.0.92
```

每次跑過 `pip install -e .` 都要重做一次。其餘見 [../SETUP.md](../SETUP.md)。
