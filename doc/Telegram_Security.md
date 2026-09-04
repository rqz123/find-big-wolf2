# Telegram Bot 与摄像头安全指南

本文说明 SmartCam Watcher 的 Telegram 安全边界、当前已实现的保护、已知风险和建议的加固方案。本文面向私人摄像头部署；Bot 的公开可发现性与摄像头访问权限是两件不同的事情。

## 1. 核心结论

- Telegram Bot 的用户名可用于搜索、提及和 `t.me` 链接，因此陌生人可能找到 Bot 并发送消息。
- 知道 Bot 用户名不等于获得摄像头权限。摄像头命令是否执行，最终由本机 SmartCam 的授权检查决定。
- Bot Token 是高敏感凭据。任何取得 Token 的人都可以控制 Bot API，必须按密码管理。
- SmartCam 当前使用 `getUpdates` 长轮询：电脑主动连接 Telegram，不向公网开放 Webhook、本机 HTTP 端口或摄像头端口。
- 摄像头图片和动画会经过 Telegram 云端；Bot 私聊不是端到端加密通道。

Telegram 官方参考：[Bot 功能与用户名](https://core.telegram.org/bots/features)、[`getUpdates`](https://core.telegram.org/bots/api#getupdates)、[Webhook](https://core.telegram.org/bots/api#setwebhook)。

## 2. 当前数据流

```text
手机用户
   │  向公开 Bot 发送命令
   ▼
Telegram Bot API
   │  SmartCam 主动长轮询 getUpdates
   ▼
私人聊天检查 → chat_id 白名单检查 → 命令解析 → 摄像头动作
     失败              失败
      └──── 静默忽略 ────┘
```

当前电脑无需接受来自互联网的入站连接。路由器不需要端口转发，也不应为 SmartCam 开放公网端口。Telegram 官方规定 `getUpdates` 与 Webhook 是互斥的两种更新接收方式。

## 3. 当前已经实现的保护

实现位置：`notify/telegram_commands.py`、`notify/telegram.py`。

### 3.1 单一私人 Chat ID 白名单

命令必须同时满足：

1. Update 包含文字消息；
2. `message.chat.type` 为 `private`；
3. `message.chat.id` 与 `telegram_credentials.yaml` 中的 `chat_id` 完全一致。

任一条件不满足，程序不会执行 `/photo`、`/clip`、`/camera`、`/pause` 或 `/auto`，也不会回复未授权聊天。陌生人不能在 Telegram 客户端中自行伪造由 Telegram 服务器写入 Update 的 Chat ID。

### 3.2 凭据隔离

- `telegram_credentials.yaml` 被 Git 忽略，不进入 GitHub。
- `config.yaml` 可以公开，但不得包含 Token、Chat ID 或用户白名单。
- 可用环境变量 `SMARTCAM_TELEGRAM_BOT_TOKEN` 和 `SMARTCAM_TELEGRAM_CHAT_ID` 覆盖凭据文件。
- 网络异常日志只记录异常类型，避免包含 Token 的 API URL 进入日志。

### 3.3 过期命令处理

程序启动时会确认并丢弃启动前积压的 Update，避免重启后执行旧的 `/camera`、`/pause` 或取图命令。运行期间使用递增 `offset` 确认已读取 Update，避免重复执行。

### 3.4 媒体与命令隔离

- 告警媒体只发送到已配置的 `chat_id`。
- 命令依次执行，避免多个远程取图操作同时争抢摄像头。
- Bot 命令菜单只是操作入口，不是授权机制；即使命令菜单可见，未授权用户仍不能执行摄像头动作。

## 4. 当前已知风险

| 场景 | 当前风险 | 说明 |
|---|---|---|
| 陌生人只知道 `@Username` | 低 | 可以发消息，但命令因 Chat ID 不匹配被忽略 |
| 陌生人猜到 Chat ID | 低 | Telegram Update 中的真实发送来源由 Telegram 填写，用户不能任意指定 |
| Bot Token 泄露 | 高 | 攻击者可调用 Bot API、消费 Update、设置 Webhook 或冒充 Bot |
| Windows 账号或凭据文件被读取 | 严重 | 攻击者同时获得 Token、目标 Chat ID 和本机访问能力 |
| `chat_id` 为空时首次自动发现 | 中高 | 若陌生人在所有者之前发送 `/start`，可能被误认为唯一私人聊天并绑定 |
| Telegram 账号被盗 | 高 | 攻击者以已授权身份发送命令 |
| 图片的云端留存 | 中 | 媒体会存在于 Telegram 云端聊天和本机 `logs/` 中 |

目前最需要加固的是首次绑定机制。已经保存正确 `chat_id` 后，自动发现不会覆盖现有值；但新安装、删除凭据或清空 Chat ID 时仍应注意抢绑风险。

## 5. 推荐的单用户安全基线

以下项目按优先级排列。第 1–3 项是建议下一阶段实现的代码改造，目前尚未全部实现。

### 5.1 禁止无条件自动绑定

生产模式下，`chat_id` 为空时应拒绝所有摄像头命令。允许以下两种绑定方式之一：

- 用户在本机手动填写 Chat ID；
- 使用本机生成的一次性配对码完成绑定。

不要采用“第一个发送 `/start` 的用户自动成为所有者”。

### 5.2 同时校验 User ID

在 Chat ID 之外增加 `message.from.id` 白名单。建议私密凭据结构：

```yaml
bot_token: "YOUR_BOT_TOKEN"
alert_chat_id: "YOUR_PRIVATE_CHAT_ID"
owner_user_id: "YOUR_TELEGRAM_USER_ID"
allowed_user_ids:
  - "YOUR_TELEGRAM_USER_ID"
```

执行命令前同时检查：

```text
chat.type == private
chat.id == alert_chat_id
from.id in allowed_user_ids
```

如果 `message.from` 缺失，也必须拒绝命令。Telegram 为 User 和 Chat 分别提供唯一 ID；显式校验两者可以减少未来扩展群组、多用户或新消息类型时的授权错误。[Telegram Bot API 类型](https://core.telegram.org/bots/api#available-types)

### 5.3 一次性配对码

推荐流程：

1. 电脑托盘生成至少 128 位随机配对码；
2. 配对码只在本机显示，有效期 5 分钟；
3. 用户通过 `/start <pairing-code>` 或 `https://t.me/<bot>?start=<pairing-code>` 提交；
4. 程序校验成功后保存 Chat ID 与 User ID；
5. 配对码立即失效，不能重复使用。

Telegram 支持向 `/start` 传递最多 64 个 base64url 字符的参数；过期、单次使用和账号绑定逻辑必须由 SmartCam 自己实现。[Telegram Deep Linking](https://core.telegram.org/bots/features#deep-linking)

### 5.4 命令限流

建议至少实施：

- `/photo`：每个授权用户每 3 秒最多一次；
- `/clip`：每个授权用户每 15 秒最多一次；
- `/camera`：每 5 秒最多一次；
- 连续失败或超限只返回简短提示，并记录安全日志。

限流不能代替鉴权，但能减轻账号误操作、客户端重试和授权账号被盗后的影响。

### 5.5 Telegram 与系统设置

- 在 BotFather 中关闭允许加入群组的选项；本项目只需要私人聊天。
- 不启用 Inline Mode、Mini App、Business Bot 或其他当前不用的能力。
- 为 Telegram 账号启用两步验证，并保护手机解锁密码。
- Windows 登录账号启用强密码，避免其他本机用户读取凭据文件。
- 不要在路由器、防火墙或云主机上为 SmartCam 开放入站端口。

Telegram 的 Privacy Mode 主要控制 Bot 在群组里能看到哪些消息，不能代替私人聊天白名单。[Telegram Privacy Mode](https://core.telegram.org/bots/features#privacy-mode)

## 6. 多用户设计

如果未来确实要允许多人使用，不应简单地接受所有 Chat ID。建议维护显式授权表：

| 角色 | 建议权限 |
|---|---|
| `owner` | `/photo`、`/clip`、`/camera`、`/pause`、`/auto`、`/status`、用户管理 |
| `viewer` | `/photo`、`/clip`、`/status` |
| 未授权用户 | 无权限、无响应 |

每个用户至少保存：`user_id`、私人 `chat_id`、角色、绑定时间、最后命令时间和启用状态。告警接收人应与命令权限分开配置，避免“可以看告警”自动等于“可以切换或暂停摄像头”。

## 7. Token 管理

### 7.1 正常要求

- Token 只保存在 `telegram_credentials.yaml`、受保护的环境变量或操作系统密钥存储中。
- 不得写入 `config.yaml`、代码、测试、日志、截图、聊天或 Git 提交。
- 不要将整个凭据文件发送给他人。
- 定期检查 `git status` 和提交内容，确认凭据文件没有被跟踪。

Telegram 官方明确提醒：任何取得 Token 的人都可以控制 Bot，因此 Token 必须安全保存。[Telegram BotFather 与 Token](https://core.telegram.org/bots/features#botfather)

### 7.2 Token 泄露处置

如果 Token 曾出现在日志、终端截图、GitHub、邮件或聊天中：

1. 立即在 BotFather 撤销旧 Token 并生成新 Token；
2. 更新 `telegram_credentials.yaml`；
3. 重启 SmartCam；
4. 检查 BotFather 设置、Webhook 状态和最近异常日志；
5. 若进入 Git 历史，仅删除当前文件不够，还需评估历史清理；
6. 重新验证 `/status`、`/photo` 和未授权用户拒绝行为。

## 8. Webhook 迁移要求

当前版本不使用 Webhook。如果未来迁移到云端 Web Service：

- 必须使用 HTTPS；
- 设置高强度 `secret_token` 并验证 `X-Telegram-Bot-Api-Secret-Token`；
- Webhook 入口仍必须执行 Chat ID、User ID、角色和限流检查；
- 验证 Update 去重，防止重复命令；
- 不把摄像头直接暴露给 Web Service，应通过经过认证、最小权限的设备通道通信；
- 不得同时运行 Webhook 与 `getUpdates`。

Telegram 官方的 `secret_token` 只证明请求知道 Webhook 密钥，不能替代最终用户授权。[Telegram `setWebhook`](https://core.telegram.org/bots/api#setwebhook)

## 9. 安全检查清单

部署或更换 Bot 后逐项确认：

- [ ] `telegram_credentials.yaml` 未被 Git 跟踪；
- [ ] Bot Token 未出现在代码、配置、日志或截图中；
- [ ] 正确的私人 Chat ID 已保存；
- [ ] 陌生账号发送 `/photo` 不会得到图片或响应；
- [ ] 群组消息不会执行命令；
- [ ] BotFather 已关闭不需要的群组和 Inline 功能；
- [ ] 只运行一个 `getUpdates` 接收实例；
- [ ] 路由器和 Windows 防火墙没有为 SmartCam 开放公网入站端口；
- [ ] Telegram 账号已启用两步验证；
- [ ] 本机 `logs/` 和 Telegram 聊天中的摄像头媒体有明确留存策略；
- [ ] Token 泄露处理流程已经知晓。

## 10. 当前建议

对于目前的单用户私人摄像头，保持现有 Chat ID 白名单，同时优先实施：

1. 删除首次无条件自动发现；
2. 增加 `message.from.id` 所有者白名单；
3. 增加一次性、短有效期配对码；
4. 增加按用户和命令分类的限流。

完成这四项后，Bot 即使被陌生人搜索到，也无法建立授权关系或执行摄像头命令。
