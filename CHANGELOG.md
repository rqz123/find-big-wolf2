# Changelog

## Unreleased

### Added

- Telegram Bot API 图片告警和自动私人 `chat_id` 发现
- Telegram Token/Chat ID 独立凭据文件及环境变量覆盖
- Telegram 设置菜单、测试通知和端到端测试脚本
- 手动暂停/恢复监控
- Telegram `/photo`、`/clip`、`/auto`、`/pause`、`/status` 和 `/help` 远程命令
- 仅允许配置的私人 Chat ID 控制摄像头
- 完整 README、Telegram 设置指南和 PRD v1.2
- 可公开提交的 `config.example.yaml`

### Changed

- 告警说明增加本地时区时间戳
- 临时网络错误、限流和服务端错误最多重试 3 次
- `config.yaml` 作为公开配置继续由 Git 跟踪；Telegram 凭据单独忽略
- Python HTTP 依赖改为 `requests`
- 自动告警由单张图片改为默认 5 秒、2 FPS 的 Telegram 动态画面，失败时回退 JPEG

### Removed

- 旧的浏览器自动化通知模块和相关依赖
- 旧通知渠道的浏览器会话缓存
