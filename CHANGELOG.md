# Changelog

## Unreleased

### Added

- Telegram Bot API 图片告警和自动私人 `chat_id` 发现
- Telegram Token/Chat ID 独立凭据文件及环境变量覆盖
- Telegram 设置菜单、测试通知和端到端测试脚本
- 手动暂停/恢复监控
- Telegram `/photo`、`/clip`、`/auto`、`/pause`、`/status` 和 `/help` 远程命令
- Telegram `/camera` 循环摄像头切换命令
- Telegram Bot 与摄像头安全指南
- 仅允许配置的私人 Chat ID 控制摄像头
- 完整 README、Telegram 设置指南和 PRD v1.2
- 可公开提交的 `config.example.yaml`
- OpenCV NanoDet + YuNet 双门槛本地人物检测、重点区域放大和人物框标注
- 触发前 3 秒低帧率证据缓存及最佳人物帧本地保存
- 历史画面分析与检测准确度调校指南
- 自动告警与人物证据图片的 7 天保留和定时清理机制
- 移动触发后最长 15 秒的逐帧人物跟踪，以及识别成功后的提前发送

### Changed

- 告警说明增加本地时区时间戳
- 临时网络错误、限流和服务端错误最多重试 3 次
- `config.yaml` 作为公开配置继续由 Git 跟踪；Telegram 凭据单独忽略
- Python HTTP 依赖改为 `requests`
- 自动告警由单张图片改为默认 5 秒、2 FPS 的 Telegram 动态画面，失败时回退 JPEG
- 命令取图会丢弃摄像头预热黑帧，并在持续黑屏时自动重建采集句柄
- 自动告警分流为：暗到亮发文字、移动但无人发文字、确认有人发 GIF
- 暗到亮与移动使用独立告警退避，避免开灯事件压制人物提醒
- 常态采样从每 30 秒一帧提高到 2 FPS；监控黑帧也会自动重开摄像头

### Removed

- 旧的浏览器自动化通知模块和相关依赖
- 旧通知渠道的浏览器会话缓存
