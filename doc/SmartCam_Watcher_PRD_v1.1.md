# SmartCam Watcher 产品需求与工程说明

版本：v1.1

平台：Windows 10/11 64 位

状态：当前实现基准

## 1. 产品目标

SmartCam Watcher 在指定时间段内监测 Windows 摄像头画面，识别光线变化和人员/物体移动，将带时间戳的抓拍图片快速推送到用户的 Telegram 手机 App。产品以系统托盘方式运行，优先满足低干扰、易配置和告警可追溯。

## 2. 使用场景

- 工作时间监测办公室、房间或门口的明显活动
- 在手机上及时收到事件类型和现场图片
- 从本机日志与抓拍文件排查漏报、误报和发送失败
- 临时暂停监控、调试预览或切换摄像头

本项目不是连续录像系统，也不替代专业安防设备。

## 3. 功能需求

### 3.1 摄像头

- 启动时扫描 OpenCV 索引 0–7，并用 Windows PnP 名称标注可用设备。
- `camera.device_index=-1` 时通过控制台完成首次选择。
- 托盘菜单支持运行时切换摄像头，并将选择写入本地配置。
- 连续 5 次读取失败后将托盘状态设为错误。

### 3.2 变化检测

- 模式 A：每 `poll_interval_sec` 秒分析一帧，减少计算量。
- 模式 B：检测到变化后按 `fps_high` 分析画面；连续 10 秒无变化后返回模式 A。
- 光线变化：比较相邻模糊灰度帧的平均亮度差。
- 局部移动：帧差二值化、膨胀、轮廓提取，并按最小面积过滤噪声。
- 第一帧只建立基线，不产生告警。

### 3.3 告警抑制

- 使用 `backoff_intervals_sec` 控制连续告警频率。
- 默认间隔配置为 5、15、30、60 分钟。
- 长时间无告警后，根据 `quiet_reset_sec` 回到初始退避级别。
- 每次实际告警将当前帧保存为 `logs/alert_YYYYMMDD_HHMMSS.jpg`。

### 3.4 Telegram 通知

- 使用 Telegram 官方 Bot API `sendPhoto` 上传本机 JPEG/PNG。
- 图片说明包含事件类型、本地日期、时间和时区。
- Bot Token 与 Chat ID 从被 Git 忽略的凭据文件读取；同名环境变量可以覆盖文件值。
- 用户向 Bot 发送 `/start` 后，程序可以自动发现唯一的私人 Chat ID。
- 如果检测到多个私人会话，必须手动指定 Chat ID，禁止猜测接收人。
- 网络连接失败、超时、HTTP 429 或 5xx 错误最多尝试配置的次数。
- 其他 4xx 配置/鉴权错误不重试，并写入日志。

### 3.5 时间调度与手动控制

- 调度器每 30 秒按本地时间检查 `schedule` 配置。
- 默认在周一至周五 09:00（含）到 18:00（不含）运行检测。
- 时间窗口外挂起检测；打开调试预览时仍允许读取画面。
- 用户可从托盘手动暂停，调度器不得自动覆盖手动暂停状态。
- 用户手动恢复后重新进入活动状态。

### 3.6 系统托盘与预览

- 绿色图标表示活动，蓝色表示挂起，红色表示摄像头错误。
- 菜单提供暂停/恢复、打开预览、选择摄像头、Telegram 设置、测试通知、打开日志和退出。
- 调试预览显示当前时间与摄像头名称。
- 预览快捷键：`Q` 关闭，`C`/`N` 下一台摄像头，`P` 上一台摄像头。
- 摄像头切换使用互斥锁和 2 秒防抖，避免重复切换竞争。

## 4. 系统架构

```text
main.py
  ├─ Config / Logger
  ├─ CameraCapture ──> MotionDetector ──> JPEG snapshot
  │                         │
  │                         └──────────> TelegramNotifier ──> Telegram App
  ├─ Scheduler ─────────────> MotionDetector suspend/resume
  └─ TrayApp <──────────────> Preview / Camera switch / Test alert
```

### 4.1 模块职责

| 模块 | 职责 |
|---|---|
| `main.py` | 初始化模块、连接回调、控制生命周期 |
| `core/camera.py` | Windows PnP 名称查询、摄像头探测和 DirectShow 采集 |
| `core/detector.py` | 双模式检测、事件分类、告警退避和抓拍 |
| `core/scheduler.py` | 本地工作时间判断及挂起/恢复 |
| `notify/telegram.py` | 凭据读取、Chat ID 发现、图片上传和网络重试 |
| `ui/tray.py` | 托盘状态、菜单操作和测试图片 |
| `ui/preview.py` | 调试画面、叠加信息和键盘切换摄像头 |
| `utils/config_loader.py` | YAML 默认值、读取和保存 |
| `utils/logger.py` | 控制台与午夜轮换文件日志 |

### 4.2 线程模型

- 主线程运行 `pystray` 事件循环。
- 检测器和调度器分别使用守护线程。
- 每次 Telegram 告警在独立守护线程执行，避免阻塞图像检测。
- 调试预览使用独立线程；摄像头切换另起短生命周期线程。

## 5. 配置与数据

- `config.example.yaml`：可提交的完整示例。
- `config.yaml`：当前公开设置和摄像头索引，由 Git 跟踪；不得包含 Token 或 Chat ID。
- `telegram_credentials.yaml`：Bot Token 和 Chat ID，Git 忽略。
- `logs/smartcam.log`：按午夜轮换，保留 7 份。
- `logs/alert_*.jpg`：实际触发的告警图片，不进入 Git。

完整字段、默认值和调优方向以根目录 `README.md` 为准。

## 6. 安全与隐私

- 任何代码、测试、日志不得输出完整 Bot Token。
- Telegram API 网络异常只记录异常类型，避免 URL 中的 Token 进入日志。
- Telegram 凭据文件必须保持在 `.gitignore` 中；公开配置不得包含任何凭据。
- 自动发现 Chat ID 只能在唯一私人会话时执行。
- Telegram Bot 私聊是云端聊天，不是端到端加密。
- 告警图片同时存在于本机日志目录与 Telegram 云端，使用者负责访问控制和留存策略。

## 7. 可靠性与失败处理

- Telegram 临时错误按 1、2、4 秒等间隔退避，服务端 `retry_after` 优先且最多等待 30 秒。
- 单次发送最终失败时保留本机抓拍，并记录错误；当前不会自动补发历史告警。
- 摄像头连续读取失败达到阈值后进入红色错误状态。
- 当前版本不会自动重新打开持续失败的摄像头，需要释放占用并重启。
- 调试预览回调异常不得终止检测线程。

## 8. 验收标准

- 在 Windows 10/11 上枚举至少一台可用摄像头。
- 工作时间内检测线程和调度线程正常启动。
- 光线或轮廓变化满足阈值后在 `logs/` 生成 JPEG。
- Telegram 测试通知能在手机收到文字与图片。
- 缺少 Token、错误 Token、无 `/start`、多个私人聊天和临时网络失败都有明确日志。
- 托盘可暂停/恢复、切换摄像头、打开/关闭预览并安全退出。
- `python -m unittest discover -s tests -v` 全部通过。

## 9. 当前限制

- 仅在 Windows DirectShow 环境验证。
- 不是连续录像、人员识别或专业入侵检测系统。
- 告警发送采用内存线程，没有持久化待发送队列。
- 尚未提供正式安装包、Windows 服务和自动升级。
- 检测效果依赖镜头、光线和阈值，需要按现场调优。

## 10. 相关文档

- 根目录 `README.md`：安装、配置、运行和排错
- `doc/Telegram_Setup.md`：手机 Bot 与凭据设置
- `CHANGELOG.md`：版本变化
