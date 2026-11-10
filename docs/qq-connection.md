# QQ 与模型连接

## 运行方式

按系统安装、扫码登录、创建两个 HTTP 连接和逐项验收的完整步骤已写入 [README 首页的 NapCat 部署与连接](../README.md#napcat-部署与连接)。本页补充协议字段和配置示例。

2026-11-10 核对的官方安装入口：[Windows 一键包](https://github.com/NapNeko/NapCatQQ/releases/latest)、[macOS 安装器](https://github.com/NapNeko/NapCat-Mac-Installer/releases/latest)、[Linux 原生安装脚本说明](https://github.com/NapNeko/NapCat-Installer)。macOS 安装器支持 macOS 12+ 的 Intel / Apple Silicon；这些是官方说明范围，不代表本项目已实测各平台的真实 QQ 登录。

QEcho 不承担 QQ 登录。请先按 [NapCat 官方安装说明](https://napneko.github.io/guide/napcat)安装桥接并自行登录 QQ，然后打开 NapCat 的网络配置。QEcho 接收好友文本私聊，使用 OneBot 11 API 发送纯文本；未授权联系人和群聊不会自动代聊。

这里的 QQ 私聊接入使用个人账号桥接，不是 QQ 开放平台的机器人账号接口。个人账号实际可用性、登录和桥接版本需要在你的环境验证。

首页“登录 QQ”默认打开 `http://127.0.0.1:6099/webui/qq_login`，可在折叠连接设置修改。NapCat 可能要求先进行 WebUI 认证、选择扫码，再由手机确认。QEcho 检测实际登录账号并同步好友，无需手填本人 QQ 号。首次部署仍需配置下面两个 HTTP 方向，仅打开登录页不会自动创建连接。

## 配置两个方向

| 方向 | NapCat 类型 | 配置 |
| --- | --- | --- |
| QEcho → NapCat，查询好友/发送消息 | HTTP 服务端 | 地址 `127.0.0.1`、端口 `3000`，设置独立 token |
| NapCat → QEcho，上报消息 | HTTP 客户端 | URL `http://127.0.0.1:8765/webhooks/onebot`，token 填 QEcho 的 webhook 令牌 |

管理控制台的访问令牌、NapCat WebUI 登录密码、HTTP 服务端 token、HTTP 客户端 token 是不同用途，不能混用。NapCat 的 HTTP 服务端 token 填入 QEcho 的“连接设置 → QQ 连接设置 → 桥接访问令牌”，服务地址填 `http://127.0.0.1:3000`。QEcho 事件令牌可直接在“QQ 连接设置”中复制，也保存在数据目录的 `webhook-token` 文件；使用 `QECHO_WEBHOOK_TOKEN` 时以该环境变量的值为准。

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

HTTP 客户端的 `reportSelfMessage` 应开启。只有桥接实际报告本人发送消息时，QEcho 才能尝试识别本人回复并自动接管；也可始终使用首页“停止”暂停全部自动回复。不同设备发送事件的实际格式和回报时序需要运行验证。QEcho 已发消息根据外部消息编号排除，避免把自身回复当成新的聊天请求。

字段来源：[NapCat 配置说明](https://github.com/NapNeko/NapCatDocs/blob/main/src/config/basic.md)。如果 NapCat 与 QEcho 位于不同容器或主机，`127.0.0.1` 指各自所在环境，需要改为双方可访问的地址；同时配置网络认证和相应的监听地址。

## 首次连接顺序

1. 在折叠“连接设置”配置真实模型、桥接服务地址/token 和登录页。保存后保持停止状态。
2. 在“QQ 连接设置”复制上报地址与事件令牌，在 NapCat 建立 HTTP 客户端。管理令牌与事件令牌分别使用。
3. 点击“登录 QQ”完成桥接登录，等待 QEcho 自动识别账号和好友；必要时点击“同步好友”。同步不自动勾选或授权。
4. 勾选一位测试好友，在统一提示词中填写人格、语气和规则，点击“保存并开始自动回复”。
5. 由测试好友发送文本私聊，在“最近运行状态”及 QQ 核对自动回复。验证后按需增加好友，修改后再次保存并开始。需要立即暂停时点击“停止”。

新首页直接使用自动回复模式，旧草稿和演练 API 仍为兼容保留。演练模型不允许启动真实自动回复。真实模型的人格效果与真实 QQ 接入仍需分别验收。

## 模型连接

设置 `model_mode=live`，填写提供聊天补全接口的服务地址、模型名和可选 API Key。地址使用服务的 API 根路径，例如 `https://your-provider.example/v1`；QEcho 追加 `/chat/completions`，发送 `model`、`messages`、`temperature`、`stream=false`，读取 `choices[0].message.content`。不要把密钥放在 URL 中。

API Key 为空时也允许连接用户自己部署、不需要认证的本地服务；是否需要密钥由对应模型服务决定。QEcho 不跟随重定向，模型失败时不把服务返回的原始响应内容写入用户可见错误，避免意外暴露密钥和消息内容。

## 发送结果与限制

QEcho 调用 [OneBot `send_private_msg`](https://github.com/botuniverse/onebot-11/blob/master/api/public.md)，使用 `auto_escape=true` 防止模型文本被解析成 CQ 操作。每次发送和查询好友前调用同规范的 `get_login_info` 核验实际登录账号，避免桥接切换账号后沿用旧配置。只有 `status=ok`、`retcode=0` 且返回有效 `message_id` 才记录成功。

桥接明确拒绝请求时记录失败。读写超时、异步受理、响应损坏、缺少消息编号等情况记录结果未知，并停止自动重试；请先在 QQ 中核对，避免重复发言。请求已经抵达 QQ 后，点击暂停无法撤回在途请求。

2026-11-07：接口和认证依据已核对；开发测试使用模拟 HTTP 响应，没有登录真实 QQ 或向真实联系人发消息。
