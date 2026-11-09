# QEcho API

所有管理请求通过 `Authorization: Bearer <admin-token>` 认证，路径以 `/api` 开头。失败返回 JSON `detail`；401 为认证失败，404 为资源不存在，409 为当前状态或规则不允许操作，422 为参数校验失败，502 为外部服务失败。

## 配置

`GET /api/settings` 返回配置及 `revision`。`PATCH /api/settings` 局部更新；字段、默认值和长度约束以 `qecho/schemas.py` 为准。未知字段拒绝，省略或 null 不修改。

模型字段：`model_mode`（`demo`/`live`）、`model_base_url`、`model_name`、`temperature`、`context_messages`、`max_reply_chars`。代聊字段：`enabled`、`owner_name`、`global_prompt`、`qq_self_id`、`bridge_base_url`、`reply_delay_seconds`。

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

`GET /api/bridge/friends` 返回 `[{"qq_id":"12345678","name":"好友称呼"}]`。查询和导入均不授权代聊。

`GET /api/status` 返回全局 `enabled`、模型模式、联系人/授权/草稿数量和连接配置是否齐全。配置齐全仅表示字段已填写，不代表网络探测或真实连接成功。

`GET /healthz` 无需认证，仅返回 `{"status":"ok"}`。

## QQ 事件

`POST /webhooks/onebot` 使用独立事件令牌，不接受管理令牌。NapCat 使用 `X-Signature: sha1=<hex>`，对原始 HTTP 正文做 HMAC-SHA1；也兼容明确携带 webhook Bearer 的其他桥接。存在签名头时必须通过签名验证，不能用 Bearer 绕过错误签名。

仅已配置 QQ 账号的好友文本私聊进入处理；事件持久化、去重后返回 `status=accepted`，后台生成回复。其他结果包括 `ignored`、`duplicate`、`takeover`、`pending_self_event`。`accepted` 表示已接收，不代表已发送或模型生成成功。
