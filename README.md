# QEcho · 回声

**你决定和谁聊，也决定如何表达。**

面向 QQ 私聊的本地代聊 Agent。每位联系人的人格、语气、关系、事实资料和额外指令均可编辑；支持草稿确认、指定联系人自动回复和人工接管。适合日常聊天、与喜欢的人保持交流、事务沟通等由你自己定义的场景。

![QEcho 管理控制台](docs/images/dashboard.png)

界面验收截图使用虚构联系人、本地模拟模型与模拟桥接，不是真实 QQ 对话。

## 本地部署

推荐每位使用者在自己的电脑上部署一个 Docker 实例，使用自己的 QQ 账号、模型密钥和聊天数据。当前版本为单用户、单 QQ 账号应用。

### 1. 准备环境

- 安装 [Git](https://git-scm.com/downloads)。
- 按 [Docker 官方说明](https://docs.docker.com/compose/install/)安装并启动 Docker 和 Compose。Windows / macOS 可使用包含 Compose 的 Docker Desktop；Windows 使用 Linux 容器。
- 确保电脑可以访问 GitHub、Docker 镜像仓库和 Python 包仓库；首次启动需要下载代码、镜像和依赖。

在终端检查环境：

```sh
git --version
docker version
docker compose version
```

`docker version` 应能显示服务端信息。Docker 部署的 Python 和应用依赖会安装在容器内，宿主机无需另装 Python 或 Node.js。

### 2. 下载并启动 QEcho

```sh
git clone https://github.com/xuhuilun/QEcho.git
cd QEcho
docker compose up -d --build
```

以下 Docker 命令均在项目的 `QEcho` 目录中执行。查看容器和启动日志：

```sh
docker compose ps
docker compose logs --tail=50 qecho
```

等待日志出现 `Application startup complete`。随后在部署这台电脑的浏览器中打开 <http://127.0.0.1:8765>，应看到管理令牌登录页。也可访问 <http://127.0.0.1:8765/healthz>，正常返回 `{"status":"ok"}`。

`compose up` 返回时应用可能还在启动；如暂时打不开页面，先检查日志并等待启动完成。默认端口只绑定本机，适合本地个人使用。

### 3. 获取令牌并登录

服务就绪后，在终端读取管理令牌：

```sh
docker compose exec qecho cat /app/.data/admin-token
```

把输出粘贴到登录页的“管理访问令牌”，点击“进入工作台”。页面将登录令牌保存在当前浏览器会话中；保存的模型密钥不会回显。

Docker 部署的数据位于容器的 `/app/.data` 和 Compose 命名卷中，宿主机项目目录的 `.data/` 不对应此卷。首次启动会生成不同用途的管理令牌与事件令牌，后面的 QQ 连接使用事件令牌。

当前 Compose 通过页面保存 AI 和 QQ 配置，**不需要复制 `.env`**。现有 `compose.yaml` 没有将 `QECHO_*` 环境变量传入容器；源码启动的 `.env` 用法见[部署与数据管理](docs/deployment.md)。

### 4. 配置 AI 并本地演练

首次登录时，全局代聊默认暂停，模型默认是无需密钥的“演练模型”。先添加一位联系人，填写 QQ 号与称呼，在右侧编辑人格、关系、语气和补充指令并保存。预设“和喜欢的人聊天”等内容均可自由修改。

在“本地演练”输入对方可能说的话，先检查页面和流程。默认演练模型返回固定格式的演示内容；要测试真实人格效果，进入左侧“设置”，填写：

| 设置项 | 填写内容 |
| --- | --- |
| 模型模式 | 真实模型 |
| 模型接口地址 | 模型服务商提供的 API 根地址，例如 `https://your-provider.example/v1`；示例域名需替换 |
| 模型名称 | 服务商实际提供的模型标识 |
| API Key | 该服务的 API 密钥；无需认证的自部署服务可留空 |
| 温度 / 上下文条数 / 回复字数上限 | 初次保留默认 `0.7` / `30` / `800` |

QEcho 会自动在根地址后追加 `/chat/completions`，读取 `choices[0].message.content`。点击“保存全部设置”，再进行一次本地演练；演练始终不会向 QQ 发消息，可以保持全局暂停。

如果模型也运行在宿主机，接口地址中的主机应使用容器可达的地址，例如 `http://host.docker.internal:模型端口/v1`，并让模型服务监听容器可访问的地址。

### 5. 接入自己的 QQ

按 [NapCat 官方安装说明](https://napneko.github.io/guide/napcat)安装并自行登录 QQ。当前 Compose 只启动 QEcho；NapCat 需单独安装、登录和配置。

下面按“QEcho 在 Docker 内，NapCat 在同一台宿主机运行”配置：

| 方向 | 配置位置 | 填写内容 |
| --- | --- | --- |
| QEcho 调用 NapCat | NapCat HTTP 服务端 | 开启服务，端口例如 `3000`，设置访问 token，监听地址需允许 QEcho 容器访问 |
| QEcho 调用 NapCat | QEcho 设置 → QQ 连接 | 本人 QQ 号、桥接地址 `http://host.docker.internal:3000`，以及上述 HTTP 服务端 token |
| NapCat 上报事件 | NapCat HTTP 客户端 | URL 填 `http://127.0.0.1:8765/webhooks/onebot`，消息格式选数组，开启 `reportSelfMessage`，token 填下述 QEcho 事件令牌 |

读取事件令牌：

```sh
docker compose exec qecho cat /app/.data/webhook-token
```

管理访问令牌、NapCat WebUI 密码、桥接 HTTP 服务端 token 和 QEcho 事件令牌各有用途，应分别配置。NapCat HTTP 客户端使用事件令牌对上报正文签名，QEcho 会验证签名。

容器内的 `127.0.0.1` 指容器自身。NapCat 若只监听宿主机回环地址，容器可能无法连接；需要配置容器可达的监听地址，同时保留 token 认证并限制网络访问范围。NapCat 也部署在容器中时，需把两者接入同一网络并使用服务名访问，详见 [QQ 接入说明](docs/qq-connection.md)。

### 6. 验收第一段真实会话

1. 在“从 QQ 导入”中选择一位测试联系人，或手动添加其 QQ 号；导入不会自动授权。
2. 保存该联系人的人格和规则，开启“允许代聊”，回复方式保持“生成草稿 · 我来确认”，确保该联系人未处于人工接管状态。
3. 开启全局代聊，由测试联系人发来一条好友文本私聊。
4. 在“QQ 会话”检查来信和回复草稿，必要时编辑，再确认发送，并到 QQ 核对实际送达。
5. 草稿流程验证后，按需把该联系人切换为自动模式；其他联系人仍需逐一明确授权。

全局暂停、联系人未授权或人工接管时，来信会被忽略，不会出现在会话页。当前只处理配置账号的好友文本私聊。用户可随时“人工接管”单个联系人，或“一键暂停”全部代聊；已经到达桥接服务的发送请求可能仍会送达。

### 日常管理与升级

查看持续日志：

```sh
docker compose logs -f qecho
```

按 `Ctrl+C` 退出日志查看，后台服务继续运行。停止和重新启动服务：

```sh
docker compose stop qecho
docker compose start qecho
```

升级前先在页面暂停代聊，停止服务并把数据复制到仓库外，再更新代码和重建。以下 `../qecho-backup` 是备份目录示例，每次备份应使用一个新的目录，备份含密钥和聊天记录：

```sh
docker compose stop qecho
docker compose cp qecho:/app/.data ../qecho-backup
git pull --ff-only
docker compose up -d --build
```

联系人、配置、密钥和聊天记录保存在 Compose 命名卷中，通常名为 `qecho_qecho-data`，以 `docker volume ls` 的实际结果为准。普通 `docker compose down` 会停止并移除容器，保留命名卷；`docker compose down -v` 会删除数据卷，不应作为日常停止或升级命令。密钥不做数据库静态加密，备份也需妥善保管。

### 常见问题

| 现象 | 检查方式 |
| --- | --- |
| `Cannot connect to the Docker daemon` | 先启动 Docker Desktop 或 Docker 服务，确认 `docker version` 能显示服务端信息 |
| 页面打不开 / 容器反复重启 | 在项目目录执行 `docker compose ps` 和 `docker compose logs --tail=100 qecho`，确认应用启动完成 |
| 8765 端口已占用 | 将 `compose.yaml` 的端口映射改为 `127.0.0.1:8766:8765` 后重新启动；浏览器地址和 NapCat 事件上报地址同步改为宿主机端口 `8766` |
| 令牌文件暂时不存在 | 先等待日志出现 `Application startup complete`，再用 `docker compose exec` 读取；Docker 部署读取的是容器内令牌 |
| AI 请求失败 | 检查 API 根地址、模型名、密钥及服务商账户状态；容器连接本机模型时检查宿主地址和监听范围 |
| 桥接登录账号不一致 | QEcho“本人 QQ 号”必须与 NapCat 当前实际登录的账号一致；更换账号前按[数据管理说明](docs/deployment.md)清理旧会话 |
| QQ 来信没有进入会话 | 检查双向地址、事件 token、本人 QQ 号、好友文本私聊范围、全局启用、联系人授权及接管状态 |
| 发送结果未知 | 先到 QQ 核对实际送达，避免重复发言；系统不会自动重试未知结果 |

本地 Python 运行和自动化测试已验证；Docker 构建文件已提供并核对，尚未完成实际容器运行验收。真实模型效果和真实 QQ 收发应在自己的环境按上述顺序验收，详见[验证记录](docs/validation.md)。

## 文档

- [产品与架构设计](docs/user-controlled-agent-design.md)
- [实施计划](docs/user-controlled-agent-plan.md)
- [QQ 接入](docs/qq-connection.md)
- [部署与数据管理](docs/deployment.md)
- [接口说明](docs/api.md)
- [验证记录与限制](docs/validation.md)

## 开发与源码运行

源码直接运行支持 macOS / Linux，需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。按上面的命令克隆仓库后，在项目目录执行：

```sh
uv sync --locked
uv run qecho
```

终端保持运行，在浏览器打开 <http://127.0.0.1:8765>；另开终端进入项目目录，执行 `cat .data/admin-token` 查看源码实例的管理令牌。源码实例的数据在宿主 `.data/` 中，与 Docker 命名卷分别保存。默认仅监听本机，使用单进程运行；同一数据目录只允许一个实例。源码实例与 Docker 实例如同时启动，需先区分端口和配置。

如需 `.env` 配置，复制 `.env.example` 后通过 `uv run --env-file .env qecho` 启动；普通 `uv run qecho` 不自动加载该文件。源码运行时，宿主机上的 NapCat 通常使用 `http://127.0.0.1:3000`，其事件上报地址仍为 `http://127.0.0.1:8765/webhooks/onebot`。

开发检查：

```sh
uv run pytest
uv run ruff check .
```

请勿把 `.data/`、`.env`、聊天记录和密钥提交到仓库。完整配置项见[部署与数据管理](docs/deployment.md)。

模型适配采用兼容 `/chat/completions` 的 HTTP 接口。QQ 适配采用外置 OneBot 11 桥接；本仓库不包含 QQ 登录器。HTTPX、FastAPI 和 Uvicorn 分别负责异步外部请求、管理接口与服务运行。SQLite 和前端无需额外服务或 Node 构建。

首次交付的自动化验证使用模拟模型与模拟桥接；真实 QQ 账号收发和真实模型的人格效果需要完成用户配置后单独验收。第三方桥接兼容性和账号可用性随 QQ 客户端、桥接版本与平台策略变化。
