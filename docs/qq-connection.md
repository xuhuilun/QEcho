# QQ 与模型连接

## 运行方式

QEcho 不承担 QQ 登录。请先按 [NapCat 官方安装说明](https://napneko.github.io/guide/napcat)安装桥接并自行登录 QQ，然后打开 NapCat 的网络配置。QEcho 接收好友文本私聊，使用 OneBot 11 API 发送纯文本；未授权联系人和群聊不会自动代聊。

这里的 QQ 私聊接入使用个人账号桥接，不是 QQ 开放平台的机器人账号接口。个人账号实际可用性、登录和桥接版本需要在你的环境验证。

## 配置两个方向

| 方向 | NapCat 类型 | 配置 |
| --- | --- | --- |
| QEcho → NapCat，查询好友/发送消息 | HTTP 服务端 | 地址 `127.0.0.1`、端口 `3000`，设置独立 token |
| NapCat → QEcho，上报消息 | HTTP 客户端 | URL `http://127.0.0.1:8765/webhooks/onebot`，token 填 QEcho 的 webhook 令牌 |

管理控制台的访问令牌、NapCat WebUI 登录密码、HTTP 服务端 token、HTTP 客户端 token 是不同用途，不能混用。QEcho 的 HTTP 服务端 token 填入“连接设置 → QQ 桥接令牌”，地址填 `http://127.0.0.1:3000`。首次运行生成的 webhook 令牌位于 QEcho 数据目录的 `webhook-token` 文件；使用 `QECHO_WEBHOOK_TOKEN` 时填入该环境变量的值。

入站 HTTP 客户端实际以 `X-Signature: sha1=<摘要>` 上报，摘要是使用 token 对**原始 HTTP 正文**计算的 HMAC-SHA1。出站调用 HTTP 服务端则使用 `Authorization: Bearer <服务端token>`。这两个认证方式不同，不能只给 HTTP 上报添加 Bearer 然后期待默认 NapCat 配置通过。

上述行为核对自 [NapCat HTTP 客户端实现](https://github.com/NapNeko/NapCatQQ/blob/98d781d19815d1625b24d717ce280ecf0a4f6d0c/packages/napcat-onebot/network/http-client.ts)和 [OneBot HTTP POST 签名规范](https://github.com/botuniverse/onebot-11/blob/master/communication/http-post.md)。

优先通过 WebUI 添加连接。以下是同机运行的配置形状，替换两个占位 token 后再保存；已有配置应只增改对应条目，不要覆盖其他连接：

```json
{
  "network": {
    "httpServers": [
      {
        "name": "qecho-api",
        "enable": true,
        "host": "127.0.0.1",
        "port": 3000,
        "enableCors": false,
        "enableWebsocket": false,
        "messagePostFormat": "array",
        "token": "REPLACE_WITH_BRIDGE_API_TOKEN",
        "debug": false
      }
    ],
    "httpClients": [
      {
        "name": "qecho-events",
        "enable": true,
        "url": "http://127.0.0.1:8765/webhooks/onebot",
        "messagePostFormat": "array",
        "reportSelfMessage": true,
        "token": "REPLACE_WITH_QECHO_WEBHOOK_TOKEN",
        "debug": false
      }
    ],
    "httpSseServers": [],
    "websocketServers": [],
    "websocketClients": [],
    "plugins": []
  }
}
```

HTTP 客户端的 `reportSelfMessage` 应开启。只有桥接实际报告本人发送消息时，QEcho 才能尝试识别本人回复并自动接管；也可始终使用控制台中的“接管”。不同设备发送事件的实际格式和回报时序需要运行验证。QEcho 已发消息根据外部消息编号排除，避免把自身回复当成新的聊天请求。

字段来源：[NapCat 配置说明](https://github.com/NapNeko/NapCatDocs/blob/main/src/config/basic.md)。如果 NapCat 与 QEcho 位于不同容器或主机，`127.0.0.1` 指各自所在环境，需要改为双方可访问的地址；同时配置网络认证和相应的监听地址。

## 首次连接顺序

1. 保持 QEcho 全局暂停，填写正在登录的 QQ 号和桥接设置。
2. 查询好友列表，选择需要代聊的联系人；此操作不会自动授权全部好友。
3. 编辑人格、语气、关系和补充指令，通过本地演练查看效果。
4. 配置真实模型，先使用草稿模式。
5. 先授权一位测试联系人，保持草稿模式并启用全局开关，由测试联系人发送私聊，确认来信与回复草稿正常。未授权或全局暂停时事件会被忽略，不会出现在会话页。确认流程正常后，再按需逐一授权其他联系人或明确切换到自动模式。

演练记录不能发送到 QQ。演练模型只证明界面和流程可用，不能证明真实模型遵从人格或真实 QQ 接入成功。

## 模型连接

设置 `model_mode=live`，填写提供聊天补全接口的服务地址、模型名和可选 API Key。地址使用服务的 API 根路径，例如 `https://your-provider.example/v1`；QEcho 追加 `/chat/completions`，发送 `model`、`messages`、`temperature`、`stream=false`，读取 `choices[0].message.content`。不要把密钥放在 URL 中。

API Key 为空时也允许连接用户自己部署、不需要认证的本地服务；是否需要密钥由对应模型服务决定。QEcho 不跟随重定向，模型失败时不把服务返回的原始响应内容写入用户可见错误，避免意外暴露密钥和消息内容。

## 发送结果与限制

QEcho 调用 [OneBot `send_private_msg`](https://github.com/botuniverse/onebot-11/blob/master/api/public.md)，使用 `auto_escape=true` 防止模型文本被解析成 CQ 操作。每次发送和查询好友前调用同规范的 `get_login_info` 核验实际登录账号，避免桥接切换账号后沿用旧配置。只有 `status=ok`、`retcode=0` 且返回有效 `message_id` 才记录成功。

桥接明确拒绝请求时记录失败。读写超时、异步受理、响应损坏、缺少消息编号等情况记录结果未知，并停止自动重试；请先在 QQ 中核对，避免重复发言。请求已经抵达 QQ 后，点击暂停无法撤回在途请求。

2026-11-07：接口和认证依据已核对；开发测试使用模拟 HTTP 响应，没有登录真实 QQ 或向真实联系人发消息。
