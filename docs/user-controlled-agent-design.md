# QEcho 用户可控代聊设计

首页流程已按后续[极简自动代聊设计](simple-automation-design.md)调整，使用好友多选、统一提示词与自动回复；本文保留初版设计和旧 API 的背景。

## 产品目标

QEcho 是本地优先、单用户、单 QQ 账号的私聊代聊应用。用户决定聊天对象、每个对象的人格、语气、关系、事实资料、额外指令以及草稿或自动发送模式。支持“与喜欢的人自然聊天”等用户自行定义的场景。预设只作为可修改的起点，不锁定人格或聊天内容。

## 第一版边界

- 只处理已配置账号的好友文本私聊；群聊、陌生人临时会话、图片、语音不自动处理。
- 联系人逐一授权，默认不授权；全局默认暂停；默认生成草稿。
- 全局基础提示词、联系人专属人格、语气、关系、事实和补充指令均可编辑，并可预览实际模型上下文。
- 用户可在会话页编辑草稿并发送、暂停单个联系人、恢复会话、一键暂停全部代聊。
- 模拟器只生成本地演练记录，永远不进入真实发送链路；清楚区分演练模型和真实模型。
- 不以固定关键词取代用户配置的聊天策略。渠道访问权限与人工接管属于程序规则，不由模型文本改变。
- 模型是否准确遵守复杂人格需要真实模型评估，不能承诺提示词具有确定性。

## 技术方案

Python 3.12+、FastAPI、Uvicorn、HTTPX、SQLite（标准库），原生 HTML/CSS/JavaScript 管理界面。FastAPI 提供接口和静态资源，HTTPX 调用模型与 QQ 桥接，单进程单工作进程运行；无需 Node 构建、向量数据库、多 Agent 框架。新增依赖分别用于 HTTP 服务、服务运行和异步外部接口。

QQ 使用外置 NapCat/兼容 OneBot 11 桥接：桥接 HTTP Client 上报事件到 `POST /webhooks/onebot`，QEcho 使用桥接 HTTP Server 的 `send_private_msg` 发送纯文本。桥接由用户安装登录，不在开发验证时操作个人账号或向联系人发送消息。

## 数据与模块

- `schemas.py`：接口校验、配置默认值。
- `store.py`：SQLite 配置、联系人、消息和草稿；联系人历史隔离、事件去重、配置版本、重启恢复。
- `engine.py`：白名单、提示词构造、顺序生成、人工接管、发送状态机。
- `adapters.py`：模型聊天接口、OneBot HTTP 调用及明确的失败/未知结果区分。
- `app.py`：应用生命周期、访问令牌认证、API、事件接收和后台任务。
- `static/`：中文控制台，联系人、会话、人格编辑、演练和连接设置。

配置包含 `enabled`、`owner_name`、`global_prompt`、`model_mode`（`demo`/`live`）、`model_base_url`、`model_name`、`temperature`、`context_messages`、`max_reply_chars`、`qq_self_id`、`bridge_base_url`、`reply_delay_seconds`。模型密钥和桥接令牌写入本地配置，普通查询仅返回是否已设置，不回传密钥。

联系人包含 `id`（本地 UUID）、`qq_id`、`name`、`enabled`、`mode`（`draft`/`auto`）、`paused`、`persona`、`tone`、`relationship`、`instructions`、`facts`。人格预设不自动覆盖用户已保存的内容。

消息包含 `id`、`contact_id`、`direction`（`incoming`/`outgoing`）、`content`、`source`（`qq`/`simulator`）、`status`、`external_id`、`created_at`、`error`、`settings_revision`、`contact_revision`。模拟会话不加入真实会话上下文。

## 权限与可靠性

管理接口使用独立 Bearer 管理令牌。NapCat 事件入口使用单独的 webhook 令牌验证原始正文的 HMAC-SHA1 `X-Signature`，同时兼容其他桥接的 Bearer 认证。首次运行生成令牌并存入受限权限的数据目录，服务默认绑定 127.0.0.1。接口不接受来自联系人消息的权限变更。

事件以 `self_id + message_id` 去重，验证配置账号、好友私聊、目标联系人白名单。不将未授权联系人内容持久化。每个会话按持久化顺序串行生成和发送，不依赖后台任务启动顺序。生成前和发送前检查最新权限与配置版本；配置修改、接管、暂停会使待发送回复失效。发送及好友查询前，使用 `get_login_info` 核验桥接实际登录 QQ 号；异步身份检查完成后再次检查发送权限。草稿编辑仅更新文本，不能跳过发送权限。

发送状态：`draft → sending → sent / error / unknown / cancelled`。网络超时或进程中断造成结果不确定时标记 `unknown`，不自动重试。重启不重放未完成的自动发送。自身已发送消息回报通过外部消息 ID 识别；在桥接提供自身消息上报的情况下，其他本人发言触发该会话接管。

程序可取消尚未开始的发送；请求已到达 QQ 后不能保证阻止其送达，此限制必须在操作说明中明确。服务只允许单进程运行，SQLite 不是分布式队列。

## API 约定

除健康检查和静态页面外，`/api/*` 需 Bearer 管理令牌；JSON 错误采用 `detail`。

| 方法与路径 | 请求/返回 |
| --- | --- |
| GET `/api/settings` | 脱敏配置，含 `model_api_key_set`、`bridge_token_set` |
| PATCH `/api/settings` | 局部配置，密钥字段可写不可读，返回脱敏配置 |
| GET `/api/contacts` | 联系人数组 |
| POST `/api/contacts` | 创建联系人，返回联系人 |
| PATCH `/api/contacts/{id}` | 局部修改，返回联系人；`paused` 控制接管 |
| DELETE `/api/contacts/{id}` | 删除联系人及其会话记录 |
| GET `/api/contacts/{id}/messages` | 消息数组，包含演练和 QQ 消息，由 `source` 区分 |
| DELETE `/api/contacts/{id}/messages` | 清理该联系人消息及待发送草稿 |
| GET `/api/contacts/{id}/prompt` | `{messages: [{role, content}]}`，实际上下文预览 |
| POST `/api/contacts/{id}/simulate` | `{text}` → 本地演练回复记录，不发 QQ |
| POST `/api/messages/{id}/send` | `{content?}` → 发送经确认草稿，重复操作不重复发 |
| POST `/api/messages/{id}/cancel` | 取消尚未发送草稿 |
| POST `/api/messages/{id}/regenerate` | 按最新规则重生成最后来信的草稿，不自动发送 |
| GET `/api/bridge/friends` | 从桥接查询好友 `{qq_id, name}` 数组，不自动授权 |
| GET `/api/status` | 全局暂停、运行模型模式、联系人/草稿数量及连接配置状态 |
| POST `/webhooks/onebot` | HMAC 签名或兼容 Bearer 认证；事件持久化后后台处理并快速应答 |
| GET `/healthz` | `{status: "ok"}`，无私密数据 |

## 验证

自动化验证：未授权联系人不生成回复、账号/群聊过滤、人格上下文、会话隔离、模拟/真实隔离、重复事件、模型错误、并发顺序、人工接管、生成途中改规则、发送前再次校验、未知发送结果与重启恢复、认证和密钥脱敏。

浏览器验证：联系人创建与编辑、人格预设、提示词预览、模拟会话、草稿操作、全局暂停、设置保存、窄屏布局。使用本地模拟外部服务验证 HTTP 链路；不把该结果写成真实 QQ 或真实模型验收。

## 一手接口依据

- [NapCat 基础配置](https://github.com/NapNeko/NapCatDocs/blob/main/src/config/basic.md)
- [OneBot HTTP 调用](https://github.com/botuniverse/onebot-11/blob/master/communication/http.md)
- [OneBot 私聊事件](https://github.com/botuniverse/onebot-11/blob/master/event/message.md)
- [OneBot 发送接口](https://github.com/botuniverse/onebot-11/blob/master/api/public.md)

2026-11-07 已核对上述公开接口说明；实际桥接版本兼容性与个人账号运行仍需用户环境验收。
