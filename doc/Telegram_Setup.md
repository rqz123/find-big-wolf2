# Telegram 通知设置

SmartCam Watcher 使用 Telegram 官方 Bot API 发送报警文字和抓拍图片。

## 首次设置

1. 在手机安装并登录 Telegram。
2. 打开 [@BotFather](https://t.me/BotFather)，发送 `/newbot`。
3. 按提示设置机器人名称和以 `bot` 结尾的用户名。
4. 复制 BotFather 返回的 Bot Token。
5. 在托盘菜单点击 **Telegram Setup**。程序会打开本说明和
   `telegram_credentials.yaml`。
6. 把 Token 填入凭据文件：

   ```yaml
   bot_token: "YOUR_BOT_TOKEN"
   chat_id: ""
   ```

7. 在手机上打开刚创建的机器人，点击 **Start** 或发送 `/start`。
8. 在托盘菜单点击 **Send Telegram Test Alert**。

程序会从 `/start` 消息自动取得私人 `chat_id`，写入凭据文件，然后发送测试图片。

## 安全说明

- `telegram_credentials.yaml` 已被 Git 忽略，不要把它提交或发送给别人。
- Bot Token 相当于机器人的密码。若意外泄露，请在 BotFather 中撤销并重新生成。
- 也可以不用凭据文件，改用环境变量：
  `SMARTCAM_TELEGRAM_BOT_TOKEN` 和 `SMARTCAM_TELEGRAM_CHAT_ID`。

## 故障排查

- 没有收到测试图片：确认已经向机器人发送 `/start`。
- 日志提示 `Unauthorized`：Token 不正确或已被撤销。
- 日志提示 `chat not found`：删除 `chat_id` 的值，再次发送 `/start` 后测试。
- 网络超时：确认电脑能够访问 `api.telegram.org`。
