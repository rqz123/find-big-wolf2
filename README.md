# SmartCam Watcher

SmartCam Watcher 是一个运行于 Windows 系统托盘的轻量级摄像头监控工具。它在指定工作时间内检测光线变化和局部移动，通过 Telegram Bot 将低帧率动态画面和时间戳发送到手机，也接受手机端的安全远程取图与模式控制命令。

## 功能

- 双模式检测：低频轮询与短时高帧率检测自动切换
- 区分全局光线变化和局部移动
- 自适应告警间隔，减少同一活动造成的重复通知
- Telegram 动态画面告警、命令取图和远程暂停/恢复
- 自动发现 Windows 摄像头，并显示设备友好名称
- 系统托盘控制：暂停/恢复、预览、切换摄像头、测试通知、打开日志
- 按星期和时间段自动启停监控
- Telegram Token 与 Chat ID 独立存放，不进入 Git

## 系统要求

- Windows 10 或 Windows 11（64 位）
- Python 3.10 或更高版本；当前已验证 Python 3.12.10
- 可被 OpenCV/DirectShow 访问的摄像头
- 可以访问 `api.telegram.org` 的网络
- Android 或 iPhone 上安装 Telegram

## 快速安装

在 PowerShell 中进入项目目录：

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果没有 `py` 命令，请先从 [python.org](https://www.python.org/downloads/windows/) 安装 64 位 Python，并在安装时包含 `pip` 和 `venv`。

首次启动：

```powershell
.\venv\Scripts\python.exe main.py
```

也可以双击 `run.bat`。当 `camera.device_index` 为 `-1` 时，程序会列出可用摄像头并要求选择；选择结果保存在本地 `config.yaml`。

## Telegram 设置

1. 在手机 Telegram 中打开带认证标记的 `@BotFather`。
2. 发送 `/newbot`，按提示创建机器人并取得 Bot Token。
3. 在系统托盘菜单选择 **Telegram Setup**。
4. 在打开的 `telegram_credentials.yaml` 中填写：

   ```yaml
   bot_token: "YOUR_BOT_TOKEN"
   chat_id: ""
   ```

5. 在手机上打开自己的机器人，点击 **Start** 或发送 `/start`。
6. 在托盘菜单选择 **Send Telegram Test Alert**。

程序会在只有一个私人会话时自动取得并保存 `chat_id`，随后发送测试图片。重新启动程序后，Bot 会注册命令菜单并开始接收命令。如果机器人收到过多个私人用户的消息，程序会拒绝自动选择，避免把摄像头图片发错人；此时需要手动填写目标 `chat_id`。

完整说明见 [Telegram 设置指南](doc/Telegram_Setup.md)；权限边界、威胁模型和加固建议见 [Telegram 安全指南](doc/Telegram_Security.md)。

> `telegram_credentials.yaml` 已被 Git 忽略。Bot Token 相当于密码，不要提交、截图或发送给他人。也可以使用环境变量 `SMARTCAM_TELEGRAM_BOT_TOKEN` 和 `SMARTCAM_TELEGRAM_CHAT_ID`，其值优先于凭据文件。

## Telegram 手机命令

Bot 只执行 `telegram_credentials.yaml` 中 `chat_id` 对应私人聊天发出的命令，其他聊天会被静默忽略。

| 命令 | 作用 |
|---|---|
| `/photo` | 立即拍摄并发送一张 JPEG 照片 |
| `/clip` | 拍摄并发送默认 5 秒、2 FPS 的动态画面 |
| `/camera` | 循环切换到下一台摄像头；只有一台时保持不变 |
| `/pause` | 暂停自动检测，进入命令取图模式；`/photo`、`/clip` 仍可用 |
| `/auto` | 恢复按 `schedule` 时间表自动检测 |
| `/status` | 查看自动/暂停状态、摄像头索引和电脑本地时间 |
| `/help` | 显示命令列表 |

动态画面在电脑端由连续 JPEG 帧生成 GIF，再通过 Telegram [`sendAnimation`](https://core.telegram.org/bots/api#sendanimation) 发送。这样比直接上传 MJPEG/AVI 更容易在 Telegram 手机端自动播放；Telegram 可能在云端将 GIF 转换为无声 MPEG-4 动画。

## 配置

仓库包含可公开的 `config.yaml`；当前摄像头索引为 `0`。在其他电脑上需要重新选择时，将 `camera.device_index` 改为 `-1`。`config.example.yaml` 是便于恢复默认值的模板。Telegram Token 和 Chat ID 不写入这两个文件。

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `schedule.start` | `09:00` | 每日监控开始时间 |
| `schedule.end` | `18:00` | 每日监控结束时间，不包含该分钟 |
| `schedule.weekdays` | `[0,1,2,3,4]` | Python 星期编号：周一为 0，周日为 6 |
| `camera.device_index` | `-1` | `-1` 表示首次启动时选择摄像头 |
| `camera.poll_interval_sec` | `30` | 模式 A 的采样间隔（秒） |
| `camera.fps_high` | `15` | 模式 B 与调试预览的目标帧率 |
| `detection.pixel_threshold` | `25` | 帧差二值化阈值；越低越敏感 |
| `detection.contour_min_area` | `800` | 最小移动轮廓面积；越低越敏感 |
| `detection.brightness_change_threshold` | `25` | 全画面平均亮度变化阈值 |
| `detection.backoff_intervals_sec` | `[300,900,1800,3600]` | 连续告警的递增抑制间隔 |
| `detection.quiet_reset_sec` | `1800` | 安静后重置告警退避的秒数 |
| `telegram.timeout_sec` | `20` | Telegram 读取超时 |
| `telegram.retries` | `3` | 临时失败最大尝试次数 |
| `telegram.command_poll_timeout_sec` | `20` | Bot 命令长轮询时长（秒） |
| `telegram.clip_duration_sec` | `5` | 动态画面时长，限制为 1–10 秒 |
| `telegram.clip_fps` | `2` | 动态画面帧率，限制为 0.5–5 FPS |
| `telegram.clip_max_width` | `640` | 动态画面最大宽度，降低上传体积 |
| `logging.level` | `DEBUG` | Python 日志级别 |

修改摄像头后，程序会把新的 `device_index` 保存到 `config.yaml`，因此 Git 工作区可能显示该文件发生变化。时间、检测阈值和 Telegram 网络参数在下次启动时生效。

## 检测与告警流程

1. 模式 A 每隔 `poll_interval_sec` 读取一帧。
2. 全局亮度变化超过阈值时分类为 `lighting`；局部轮廓超过面积阈值时分类为 `movement`。
3. 检测到变化后进入模式 B，按 `fps_high` 读取画面。
4. 模式 B 连续 10 秒没有变化后回到模式 A。
5. 满足告警间隔时，将 JPEG 保存到 `logs/alert_YYYYMMDD_HHMMSS.jpg`。
6. 后台线程继续低帧率取图并生成几秒 GIF，通过 Telegram `sendAnimation` 发送；生成失败时回退到原始 JPEG。
7. 命令监听线程通过 `getUpdates` 长轮询接收手机命令，只接受配置的私人 `chat_id`。
8. 网络超时、Telegram 限流或服务端错误会指数退避重试；配置或鉴权错误直接记录日志。

## 系统托盘

| 菜单 | 作用 |
|---|---|
| `Pause/Resume Monitoring` | 手动暂停或恢复检测；手动暂停不会被调度器自动取消 |
| `[Debug] Open/Close Video Preview` | 打开或关闭实时调试画面 |
| `Select Camera` | 切换摄像头并保存选择 |
| `Telegram Setup` | 创建并打开凭据文件和设置指南 |
| `Send Telegram Test Alert` | 生成测试图片并验证 Telegram |
| `Open Logs` | 打开日志和告警抓拍目录 |
| `Quit` | 关闭预览、调度器、检测器和摄像头 |

托盘颜色：绿色表示监控中，蓝色表示挂起，红色表示摄像头错误。调试预览获得焦点时可用 `Q` 关闭，`C`/`N` 切换下一台摄像头，`P` 切换上一台摄像头。

## 日志和排错

日志写入 `logs/smartcam.log`，每天午夜轮换并保留 7 份。`logs/` 不进入 Git。

- 没有托盘图标：查看任务栏隐藏图标区域，并检查 `logs/smartcam.log`。
- 找不到摄像头：关闭占用摄像头的软件，确认 Windows 摄像头权限后重启。
- `/photo` 或 `/clip` 返回纯黑：程序会丢弃预热黑帧，并在连续黑帧时自动重新打开当前摄像头；查看日志是否出现 `reopening it`。若仍失败，再用托盘预览确认镜头隐私设置。
- Telegram 没有收到图片：先向机器人发送 `/start`，再运行测试通知。
- 日志出现 `Unauthorized`：在 BotFather 重新生成 Token 并更新凭据文件。
- 日志出现 `chat not found`：清空 `chat_id`，重新发送 `/start` 后再测试。
- 日志出现 `Conflict` 或 HTTP 409：确认同一个 Bot 没有在另一台电脑或另一份程序中同时接收命令。
- 网络超时：确认电脑可以访问 `https://api.telegram.org`。
- 误报过多：提高 `pixel_threshold` 或 `contour_min_area`。
- 漏报：降低上述两个阈值，但需观察噪声和光线变化。

## 测试

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

在已配置真实 Telegram 凭据时，可运行：

```powershell
.\venv\Scripts\python.exe tests\send_telegram_test.py
```

该命令会向配置的私人聊天发送一张生成的蓝色测试图片。

## 项目结构

```text
find-big-wolf2/
├── core/                       摄像头、检测器、动态画面和命令控制
├── notify/telegram.py          Telegram Bot API 消息与媒体发送
├── notify/telegram_commands.py Telegram 命令长轮询和访问控制
├── ui/                         系统托盘和调试预览
├── utils/                      配置与日志
├── tests/                      Telegram 单元测试和端到端测试脚本
├── doc/                        设置指南与产品需求文档
├── config.example.yaml         可提交的配置模板
├── config.yaml                 当前公开配置（Git 跟踪）
├── telegram_credentials.yaml   私密凭据（Git 忽略）
├── requirements.txt
├── run.bat
└── main.py
```

更详细的产品和工程约束见 [PRD v1.2](doc/SmartCam_Watcher_PRD_v1.2.md)。

## 当前限制

- 仅针对 Windows 和 DirectShow 摄像头验证。
- Telegram Bot 私聊属于 Telegram 云端聊天，不是端到端加密。
- 告警图片会保留在本机 `logs/`；需要自行制定清理或归档策略。
- 摄像头在运行中持续读取失败时会进入错误状态，当前需要排除占用后重启程序。
- `getUpdates` 长轮询要求同一个 Bot 同时只能由这一份 SmartCam 程序接收更新。
- 项目目前以源码和虚拟环境运行，尚未提供正式安装包或 Windows 服务。
