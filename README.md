# QEcho · 回声

**你决定和谁聊，也决定如何表达。**

面向 QQ 私聊的本地代聊 Agent。每位联系人的人格、语气、关系、事实资料和额外指令均可编辑；支持草稿确认、指定联系人自动回复和人工接管。适合日常聊天、与喜欢的人保持交流、事务沟通等由你自己定义的场景。

![QEcho 管理控制台](docs/images/dashboard.png)

界面验收截图使用虚构联系人、本地模拟模型与模拟桥接，不是真实 QQ 对话。

## 启动

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。支持 macOS / Linux，默认仅监听本机，单进程运行。

```sh
git clone https://github.com/xuhuilun/QEcho.git
cd QEcho
uv sync --locked
uv run qecho
```

打开 <http://127.0.0.1:8765>，另开终端读取管理令牌并在页面登录：

```sh
cat .data/admin-token
```

首次启动会自动创建受限权限的数据目录和两个不同用途的令牌。请勿把 `.data/`、`.env`、聊天记录和密钥提交到仓库。

## 开始使用

1. 新建联系人，填写 QQ 号、称呼与关系，编辑其专属人格、语气和补充指令。
2. 在“本地演练”中测试。默认演练模型不调用外部服务，不会向 QQ 发消息。
3. 在设置中配置模型服务的 Base URL、模型名称和密钥，切换到真实模型；演练仍然不会外发。
4. 按 [QQ 接入说明](docs/qq-connection.md) 安装并登录 NapCat，配置 OneBot 双向 HTTP 连接和本人的 QQ 号。
5. 明确授权联系人，选择草稿或自动模式，再开启全局代聊。

所有人格预设都可以改写。自动发送的对象范围与暂停状态由程序检查，聊天消息或提示词无法扩大授权范围。界面上的“接管”会暂停该联系人；“全局暂停”阻止后续发送。已经到达桥接服务的请求可能仍会送达。

## 文档

- [产品与架构设计](docs/user-controlled-agent-design.md)
- [实施计划](docs/user-controlled-agent-plan.md)
- [QQ 接入](docs/qq-connection.md)
- [部署与数据管理](docs/deployment.md)
- [接口说明](docs/api.md)
- [验证记录与限制](docs/validation.md)

## 开发

```sh
uv sync --locked
uv run pytest
uv run ruff check .
```

模型适配采用兼容 `/chat/completions` 的 HTTP 接口。QQ 适配采用外置 OneBot 11 桥接；本仓库不包含 QQ 登录器。HTTPX、FastAPI 和 Uvicorn 分别负责异步外部请求、管理接口与服务运行。SQLite 和前端无需额外服务或 Node 构建。

首次交付的自动化验证使用模拟模型与模拟桥接；真实 QQ 账号收发和真实模型的人格效果需要完成用户配置后单独验收。第三方桥接兼容性和账号可用性随 QQ 客户端、桥接版本与平台策略变化。
