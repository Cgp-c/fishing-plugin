# 自动钓鱼插件（fishing-plugin）

手机钓鱼小游戏的自动钓鱼插件：**电脑端 OpenCV 检测核心 + 安卓 adb / 鸿蒙 hdc 设备桥**。
不修改游戏、不读内存、不往手机装任何软件——只是"看屏幕 + 替你点屏幕"。

配套测试游戏（图像识别测试台）：[fishing-game-verify](https://github.com/Cgp-c/fishing-game-verify)

```
┌─────────── 电脑（本插件，Python）───────────┐        USB 数据线        ┌──── 手机 ────┐
│ 截屏 ← adb/hdc screencap │ OpenCV 检测 │ 决策 │ ──仅指令/位图，不走网络──→ │ 游戏前台运行  │
│ 模拟人手点击 → adb/hdc input tap（随机延迟/随机点）│                      │ 不装任何软件  │
└────────────────────────────────────────────┘                          └──────────────┘
```

## 功能

- **三态识别**：准备钓鱼（READY）/ 钓鱼等待（WAITING）/ 鱼获结算（CATCH）
  - READY：抛竿提示文字区"与水底的色距"列覆盖率判定
  - CATCH：结算态全屏压暗 → 全帧平均亮度骤降（实测 66.8 vs 平时 ~196）
  - 主锚点"求福"图标模板匹配（TM_CCOEFF_NORMED ≥ 0.85，实测命中 ≈0.99）
- **鱼获分色**：名字条带内 HSV 直方图峰值分类（白/绿/蓝/紫/黄）
  - **紫、黄 → 自动暂停 + 响铃**，等你手动处理完自动继续
  - 分色只在名字条带小区域做（条带外金色装饰 100 万+ 像素必误报）
- **自动出售 / 提交订单**：按左下按钮颜色区分（蓝=出售，橙金=提交订单）
- **安全暂停链路**：求福锚点连续 3 帧缺失（误触弹窗/切屏）→ 立即暂停 + 通知，
  回到钓鱼界面自动继续
- **全局热键**：F9 暂停/继续（轮询实现，无需管理员权限）；Ctrl+C 退出
- **拟人化**：SAFE_CAST 安全区内随机取点、随机延迟、动作冷却——降低行为特征
- **设备桥**：`pc`（窗口，开发测试用）/ `adb`（安卓）/ `hdc`（鸿蒙，待实机联调）

## 安装

Python 3.10+（开发环境为 3.14），依赖锁定版本：

```bash
pip install -r requirements.txt      # numpy / opencv-python / Pillow + 标准库，仅此三项
```

## 使用

### 1. 先在电脑上用测试游戏验证（推荐第一步）

双击 `fishing-game-verify/启动游戏.bat` 开游戏，然后：

```bash
python -m fishing_plugin                    # 默认 pc 桥，对测试游戏窗口跑
python -m fishing_plugin --dry-run          # 只检测不点击，看看判定对不对
python -m fishing_plugin --debug-save       # 截图落盘 debug/ 用于排查
```

### 2. 真机（安卓）

1. 手机开启「USB 调试」，数据线连接，`adb devices` 能看到设备
2. 首次建议 dry-run：

```bash
python -m fishing_plugin --device adb --dry-run --duration 60
```

3. 正式运行（把序列号写进 config.local.json 或命令行传入）：

```bash
python -m fishing_plugin --device adb --serial <你的序列号>
```

### 3. 真机（鸿蒙）

需先安装华为 hdc（HarmonyOS Device Connector，开发者渠道）。

```bash
python -m fishing_plugin --device hdc --serial <connect key> \
    --template templates/qiufu_real.png --ref-width 929
```

⚠ `hdc` 桥已按公开命令规范实现（snapshot_display / uitest uiInput / power-shell），
**尚未实机联调**——首次连接时建议务必 `--dry-run` 观察检测是否正常，再放行点击。

### 配置

检测阈值/坐标/延迟全部在 `config.json`（本地文件，**无任何账号字段**）。
个人设备序列号可放 `config.local.json`（已被 .gitignore 排除）。
真实游戏模板 `templates/qiufu_real.png` 由 `tools/make_template.py` 从你的截图裁出
（不同分辨率手机会自动按帧宽缩放模板）。

## 测试

```bash
python -m unittest discover -s tests        # 18 项：检测离线校准 + 拟人化 + 白名单 + 端到端
python tests/test_e2e_game.py               # 单跑端到端（会弹游戏窗口并动鼠标）
```

- `test_detector_offline`：用三张真实游戏截图逐帧验证检测核心
- `test_e2e_game`：黑盒驱动测试游戏全流程——五色序列 / 紫黄暂停 / 误触链路 /
  F9 热键 / 审计核对（所有点击都在允许区域内）

---

## 安全与隐私规范（强制）

### 信息不出本地

| 项目 | 约束 |
|---|---|
| 网络行为 | **零联网**：无遥测、无统计上报、无"检查更新"回连、无云 OCR/云识别（全部本地 OpenCV）。代码不 import 任何网络库 |
| 截图 | 默认**只在内存中处理即丢弃**；仅 `--debug-save` 保存到本地 `debug/`。hdc 截图因系统命令限制需经临时文件中转，读入内存后立即删除（唯一例外，见上） |
| 日志 | 仅状态机事件与命令流水（本地 `logs/`），不含账号、不含截图；`清理.bat` / `--purge` 一键清理 |
| 配置 | 检测阈值/坐标全部本地 `config.json`，无账号密码字段——本插件**不需要也不保存任何游戏账号信息** |
| 代码 | 全程开源可审计，依赖仅 opencv/numpy/pillow + 标准库并锁定版本；不加壳不混淆 |

### 手机端最小权限与防扒窃

- 手机上**只**使用系统自带的调试接口（USB 调试/开发者模式）；不装 APK、不用无障碍、不 root、不读手机存储/通讯录/短信/剪贴板、不写入任何文件（hdc 临时截图文件用完即删）
- 发往手机的命令走**白名单**（仅 `screencap`/`snapshot_display`、`input tap`/`uitest click`、`wm size`、亮屏），其余一律拒绝执行并写审计日志（见 `tests/test_devices.py`）
- 连接前**校验设备序列号**：只对登记过的设备发指令，防止误控其他设备
- 所有命令写入 `logs/audit.log`，可随时核对插件到底对手机做了什么
- 用完即关：`--kill-server` 可在退出时 `adb kill-server`/`hdc kill`；**记得关闭手机上的 USB 调试**
- 定期在手机「开发者选项 → 撤销 USB 调试授权」清理旧授权（防调试密钥被冒用）

### 关于 USB 调试的固有暴露面（系统机制，与插件无关）

USB 调试开启期间，**电脑上任何软件**都能接触该接口。最有效的防护就是"用完就关"。
本插件不开启 adb TCP 网络模式（拒绝 `adb tcpip 5555` 之类的命令）。

### 行为边界

- 只对已登记设备的游戏画面操作，不触及其他应用
- 插件不请求管理员权限、无开机自启、无后台驻留、无系统注入，进程退出即完全消失

## 已知风险（诚实声明）

- **封号风险**：纯外部方案不修改游戏、不读内存，客户端层面无痕；但服务端理论上可通过
  行为分析（规律点击/超长在线）识别脚本。插件的随机化只能**降低**风险，**无法归零**。
  建议控制使用时长、模拟人类作息、重要账号谨慎使用。
- 检测阈值基于当前游戏版本实测校准，游戏改版后可能需要重新校准
  （`tools/make_template.py` 重新裁模板 + 调 `config.json` 阈值）。

## 许可

MIT（见 LICENSE）。`templates/qiufu_real.png` 为用户自有游戏截图裁片，仅供个人检测用途。
