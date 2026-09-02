# Changelog

## Unreleased

### Added

- Telegram Bot API 图片告警和自动私人 `chat_id` 发现
- Telegram Token/Chat ID 独立凭据文件及环境变量覆盖
- Telegram 设置菜单、测试通知和端到端测试脚本
- 手动暂停/恢复监控
- 完整 README、Telegram 设置指南和 PRD v1.1
- 可公开提交的 `config.example.yaml`

### Changed

- 告警说明增加本地时区时间戳
- 临时网络错误、限流和服务端错误最多重试 3 次
- 本机 `config.yaml` 和 Telegram 凭据不再进入 Git
- Python HTTP 依赖改为 `requests`

### Removed

- 旧的浏览器自动化通知模块和相关依赖
- 旧通知渠道的浏览器会话缓存
