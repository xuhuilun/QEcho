# QEcho API

所有管理请求通过 `Authorization: Bearer <admin-token>` 认证，路径以 `/api` 开头。失败返回 JSON `detail`；401 为认证失败，404 为资源不存在，409 为当前状态或规则不允许操作，422 为参数校验失败，502 为外部服务失败。

## 极简自动代聊入口

下面接口均需管理令牌：

| 接口 | 作用 |
| --- | --- |
| `GET /api/automation` | 返回 `running/account_id/selected_ids/paused_ids/active_count/prompt/revision/model_ready/login_url/recent` 和配置来源、启动状态，最近最多 8 条回复状态，不返回聊天正文或密钥 |
| `POST /api/qq/sync` | 自动识别桥接登录账号并获取好友，返回上述状态及 `friends: [{qq_id, name}]`，不自动授权 |
| `POST /api/automation/start` | 请求 `qq_ids`、非空 `prompt`、`expected_revision`；验证实际好友列表后原子替换授权范围和统一提示词，所选好友使用自动回复模式 |
| `POST /api/automation/stop` | 立即暂停并取消未发送任务，不等待桥接、模型或会话锁 |
| `GET /api/setup` | 仅供已认证主人配置桥接上报，返回 `webhook_path` 与 `webhook_token`；不要分享返回值 |

启动使用完整好友名单，最多 500 位，QQ 号不能重复。配置或联系人状态在异步核验期间改变，启动失败并保持暂停；更晚的人工接管不会被旧启动覆盖。已有真实 QQ 历史时不能直接换号复用。

`simple_mode=true` 时统一提示词不叠加旧联系人人格/语气/资料，旧数据不删除。原有会话 API 继续保留兼容，首页以极简自动代聊为主。

## 文件配置模式

使用 `qecho.toml` 时，状态增加 `config_source="file"`、`config_file`（仅文件名）、`configured_ids`（文件指定的 QQ 号）、`startup_state` 和 `startup_error`；网页模式为 `config_source="web"`、`startup_state="manual"`。QQ 同步和停止也返回这些状态字段。`selected_ids` 表示已生效授权，等待期间可为空；`configured_ids` 表示计划名单，两者不要混淆。

启动状态为 `disabled`（未开启自动启动）、`waiting`（等待桥接和登录）、`starting`（核验账号/好友）、`running`（已成功启动）、`stopped`（本轮已停止）、`failed`（需修正并重启）。实际是否仍在自动回复以 `running` 布尔值为准，启动状态记录协调任务的结果。

文件模式允许已认证的所有 GET、`POST /api/qq/sync`、`POST /api/automation/stop`。其他管理写请求统一返回 409，包括 settings、联系人、消息清理/发送/取消/重新生成/演练、任意名单开始请求；错误提示编辑文件并重启。事件入口不受该管理写限制，仍使用独立事件认证。

同步与后台启动串行执行，停止独立提交暂停和版本屏障并取消启动任务，无需等待网络。停止后不能通过开始 API 恢复，重新启动进程才会重新执行文件计划。配置优先级和密钥留空行为见[配置文件使用说明](file-configuration.md)。

## 配置字段

`GET /api/settings` 返回配置及 `revision`。`PATCH /api/settings` 局部更新；字段、默认值和长度约束以 `qecho/schemas.py` 为准。未知字段拒绝，省略或 null 不修改。

模型字段：`model_mode`（`demo`/`live`）、`model_base_url`、`model_name`、`temperature`、`context_messages`、`max_reply_chars`。代聊字段：`enabled`、`owner_name`、`global_prompt`、`qq_self_id`、`bridge_base_url`、`bridge_login_url`、`simple_mode`、`reply_delay_seconds`。极简单页通过账号同步自动填写 `qq_self_id`。

`model_api_key` 与 `bridge_token` 只写；读取仅返回 `model_api_key_set`、`bridge_token_set`。提交空字符串保留已有密钥；使用 `clear_model_api_key: true` 或 `clear_bridge_token: true` 明确删除。所有配置保存增加版本并取消未发送草稿。

## 联系人

- `GET /api/contacts`：直接返回联系人数组。
- `POST /api/contacts`：至少提供字符串 `qq_id` 和 `name`。返回联系人（201）。
- `PATCH /api/contacts/{id}`：修改 `name`、`enabled`、`paused`、`mode`、`persona`、`tone`、`relationship`、`facts`、`instructions`。
- `DELETE /api/contacts/{id}`：删除联系人及会话。

默认 `enabled=false`、`paused=false`、`mode=draft`。`id` 是本地 UUID，`qq_id` 是 QQ 号。联系人 QQ 号不允许换成其他账号；人格和规则修改会增加版本并取消旧草稿。`paused=true` 表示人工接管。

## 会话与草稿

`GET /api/contacts/{id}/messages` 返回该联系人的消息数组。`DELETE` 同一路径清理会话。消息的 `source` 为 `qq` 或 `simulator`，`direction` 为 `incoming` 或 `outgoing`。`parent_id` 标记回复对应的来信。

```json
{
  "id": "local-message-uuid",
  "contact_id": "local-contact-uuid",
  "direction": "outgoing",
  "source": "qq",
  "content": "好呀，下午一起去？",
  "status": "draft",
  "external_id": null,
  "error": ""
}
```

- `POST /api/contacts/{id}/simulate`，正文 `{"text":"对方可能说的话"}`：返回演练回复记录，不发送 QQ。
- `GET /api/contacts/{id}/prompt`：返回 `{"messages":[{"role":"system","content":"..."}]}`。只预览真实 QQ 上下文，演练历史独立。
- `POST /api/messages/{id}/send`，正文 `{}` 或 `{"content":"编辑后的回复"}`：只发送符合最新权限与配置版本的真实 QQ 草稿；重复确认已发送记录不会再次发送。
- `POST /api/messages/{id}/cancel`：取消尚未发送草稿。
- `POST /api/messages/{id}/regenerate`：只为最后一条 QQ 来信重新生成，始终留草稿，不自动发；正在发送或结果未知的记录不能以此绕过核对。

状态包括 `received`、`processing`、`processed`、`generating`、`draft`、`sending`、`sent`、`error`、`unknown`、`cancelled`。`unknown` 不代表失败，不允许直接重发。

## 连接与状态

`GET /api/bridge/friends` 在已配置本人 QQ 号且桥接实际登录账号一致时，返回 `[{"qq_id":"12345678","name":"好友称呼"}]`。查询和导入均不授权代聊。

`GET /api/status` 返回全局 `enabled`、模型模式、联系人/授权/草稿数量和连接配置是否齐全。配置齐全仅表示字段已填写，不代表网络探测或真实连接成功。

`GET /healthz` 无需认证，仅返回 `{"status":"ok"}`。

## QQ 事件

`POST /webhooks/onebot` 使用独立事件令牌，不接受管理令牌。NapCat 使用 `X-Signature: sha1=<hex>`，对原始 HTTP 正文做 HMAC-SHA1；也兼容明确携带 webhook Bearer 的其他桥接。存在签名头时必须通过签名验证，不能用 Bearer 绕过错误签名。

仅已配置 QQ 账号的好友文本私聊进入处理；事件持久化、去重后返回 `status=accepted`，后台生成回复。其他结果包括 `ignored`、`duplicate`、`takeover`、`pending_self_event`。`accepted` 表示已接收，不代表已发送或模型生成成功。
