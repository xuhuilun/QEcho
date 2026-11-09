# QEcho · 回声

**你决定和谁聊，也决定如何表达。**

面向 QQ 私聊的本地代聊 Agent。每位联系人的人格、语气、关系、事实资料和额外指令均可编辑；支持草稿确认、指定联系人自动回复和人工接管。适合日常聊天、与喜欢的人保持交流、事务沟通等由你自己定义的场景。

![QEcho 管理控制台](docs/images/dashboard.png)

界面验收截图使用虚构联系人、本地模拟模型与模拟桥接，不是真实 QQ 对话。

## 本地部署

默认使用 **Python 3.12 + uv** 在自己的电脑上运行，一个实例管理一个 QQ 账号。先启动工作台和本地演练，再配置真实 AI 与 QQ 接入。

当前源码运行支持 **macOS / Linux**，已在 macOS 完成启动验收。Windows 原生运行尚不支持，需使用 Linux 环境；本文不将 Windows 原生启动列为已验证能力。

### 1. 准备 Git 和 uv

安装 [Git](https://git-scm.com/downloads)，再按 [uv 官方说明](https://docs.astral.sh/uv/getting-started/installation/)安装 uv。macOS / Linux 可以使用以下官方安装命令：

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

安装后重新打开终端，确认命令可用：

```sh
git --version
uv --version
```

Python 3.12 由下面的 uv 命令安装和管理，应用依赖按 `uv.lock` 安装；前端直接由后端提供，无需 Node.js 构建。

### 2. 下载项目并安装 Python

```sh
git clone https://github.com/xuhuilun/QEcho.git
cd QEcho
uv python install 3.12
```

`uv python install 3.12` 安装项目使用的 Python 3.12。后续操作均在 `QEcho` 项目目录中执行。

### 3. 下载并安装 Python 依赖

**先安装项目依赖，再启动 QEcho。** 在项目目录执行：

```sh
uv sync --locked
```

这条命令会创建项目专用的 `.venv` 虚拟环境，按 `uv.lock` 下载并安装所需版本的 Python 包，同时安装 QEcho 本身及启动命令。已有缓存会复用；首次安装需要网络访问包下载源。

主要运行依赖如下，间接依赖也会一起安装：

| Python 包 | 用途 |
| --- | --- |
| FastAPI | 管理接口和工作台服务 |
| Uvicorn | 运行 QEcho HTTP 服务 |
| HTTPX | 请求 AI 模型与 QQ 桥接接口 |

`pyproject.toml` 声明依赖，`uv.lock` 锁定安装版本。默认还会安装仓库中的开发检查工具。无需逐个下载这些包，也无需手动激活 `.venv`。

安装命令成功后，检查项目解释器和依赖：

```sh
uv run --no-sync python --version
uv run --no-sync python -c "import fastapi, httpx, uvicorn, qecho; print('Python 依赖安装成功')"
```

应看到 Python `3.12.x` 和 `Python 依赖安装成功`。这里的 `--no-sync` 只检查已经安装的环境，不在检查过程中补装依赖。安装或检查报错时，先完成依赖问题排查，再进行下一步。

### 4. 启动 QEcho

```sh
uv run qecho
```

看到 `Application startup complete` 后，保持这个终端运行，在浏览器打开 <http://127.0.0.1:8765>，应显示管理令牌登录页。访问 <http://127.0.0.1:8765/healthz> 会返回 `{"status":"ok"}`。默认仅监听本机，关闭启动终端或按 `Ctrl+C` 会停止服务。

### 5. 查看管理令牌并登录

另开一个终端，进入刚才克隆的 `QEcho` 目录，读取令牌：

```sh
cat .data/admin-token
```

把输出填入页面的“管理访问令牌”，点击“进入工作台”。页面将登录令牌保存在当前浏览器会话中；保存的模型密钥不会回显。

首次启动会在项目的 `.data/` 目录生成数据库、`admin-token` 管理令牌和 `webhook-token` 事件令牌。管理令牌用于登录，事件令牌用于 QQ 消息上报，两者应分别配置。`.data/` 包含配置、聊天记录和密钥，默认已被 Git 忽略，不要公开分享。

### 6. 配置人格与 AI

新建联系人，填写 QQ 号和称呼，在右侧设置人格、关系、语气、已确认信息和补充指令并保存。“和喜欢的人聊天”等预设只是起点，所有文本均可编辑。

初始状态是全局暂停、联系人未授权和无需密钥的“演练模型”。在“本地演练”输入对方可能说的话，默认会返回固定格式的演示内容，先确认页面和流程正常。

要测试真实 AI，进入左侧“设置”，填写：

| 设置项 | 填写内容 |
| --- | --- |
| 模型模式 | 真实模型 |
| 模型接口地址 | 服务商的 API 根地址，例如 `https://your-provider.example/v1`；示例域名需替换为真实服务地址 |
| 模型名称 | 服务商实际提供的模型标识 |
| API Key | 对应服务的密钥；无需鉴权的本机模型服务可留空 |
| 温度 / 上下文条数 / 回复字数上限 | 初次保留默认 `0.7` / `30` / `800` |

QEcho 自动在根地址后追加 `/chat/completions`，读取 `choices[0].message.content`。保存全部设置后，再进行本地演练，查看真实模型是否符合人格与语气。演练始终不向 QQ 发消息，可以保持全局暂停。

如果模型也在同一台电脑上直接运行，使用模型服务实际监听的本机地址，例如 `http://127.0.0.1:模型端口/v1`，其中端口需替换为真实端口。

### 7. 连接本机 QQ

按 [NapCat 官方安装说明](https://napneko.github.io/guide/napcat)安装桥接，并自行登录需要代聊的 QQ。QEcho 和 NapCat 分别启动，按“在同一台电脑上直接运行”配置以下两个方向：

| 方向 | 配置位置 | 填写内容 |
| --- | --- | --- |
| QEcho 调用 NapCat | NapCat HTTP 服务端 | 开启服务，例如监听 `127.0.0.1:3000`，设置访问 token |
| QEcho 调用 NapCat | QEcho 设置 → QQ 连接 | 本人 QQ 号、桥接地址 `http://127.0.0.1:3000`、上述 HTTP 服务端 token |
| NapCat 上报事件 | NapCat HTTP 客户端 | URL 填 `http://127.0.0.1:8765/webhooks/onebot`，消息格式选数组，开启 `reportSelfMessage`，token 填 QEcho 的事件令牌 |

在项目目录读取事件令牌：

```sh
cat .data/webhook-token
```

NapCat 使用事件令牌对上报正文签名，QEcho 会验证签名。管理访问令牌、NapCat WebUI 密码、HTTP 服务端 token 和 QEcho 事件令牌各有用途。完整配置及示例见 [QQ 接入说明](docs/qq-connection.md)。

如果 QEcho 与 NapCat 不在同一个系统网络环境，例如分别位于虚拟机、容器或不同电脑，需要把地址改为双方实际可达的地址；`127.0.0.1` 只表示各自环境自身。

### 8. 验收第一段真实会话

1. 在“从 QQ 导入”中选择一位测试联系人，或手动添加 QQ 号；添加和导入都不会自动授权。
2. 保存人格和规则，开启该联系人的“允许代聊”，保持“生成草稿 · 我来确认”，确保未处于人工接管状态。
3. 开启全局代聊，由测试联系人发来一条好友文本私聊。
4. 在“QQ 会话”检查来信和草稿，必要时编辑，再确认发送，并到 QQ 核对送达。
5. 草稿流程正常后，按需切换该联系人为自动模式；其他联系人仍需逐一授权。

全局暂停、联系人未授权或处于人工接管时，来信会被忽略，不会出现在会话页。当前只处理配置账号的好友文本私聊。用户可随时接管单个联系人或暂停全部代聊；已经到达桥接服务的发送请求可能仍会送达。

### 更换端口与可选配置

日常 AI、QQ 和联系人配置通过工作台保存。默认无需 `.env`；如需更换端口或数据目录，先在运行终端按 `Ctrl+C` 停止服务，再复制配置：

```sh
cp .env.example .env
```

编辑 `.env`，例如把 `QECHO_PORT=8765` 改为 `QECHO_PORT=8766`，然后显式加载配置启动：

```sh
uv run --env-file .env qecho
```

对应浏览器地址改为 `http://127.0.0.1:8766`，NapCat 事件上报地址同步改为 `http://127.0.0.1:8766/webhooks/onebot`。普通 `uv run qecho` 不自动加载 `.env`。更换数据目录或覆盖令牌后，读取和使用实际配置的目录或环境变量，详见[部署与数据管理](docs/deployment.md)。

### 停止、备份与升级

在运行 QEcho 的终端按 `Ctrl+C` 停止；再次在项目目录执行 `uv run qecho` 即可启动。若使用 `.env`，恢复时仍使用 `uv run --env-file .env qecho`。

升级前先在页面暂停代聊，再停止服务并备份整个 `.data/`。下面的 `../qecho-backup` 是仓库外备份目录示例，每次备份应使用新的目录；备份包含密钥和聊天记录。以下命令用于默认配置，使用 `.env` 时应将最后一行换成 `uv run --env-file .env qecho`：

```sh
cp -R .data ../qecho-backup
git pull --ff-only
uv sync --locked
uv run qecho
```

使用自定义数据目录时，备份实际的数据目录。保持原数据目录会保留联系人、人格、令牌及会话记录。数据库未做静态加密，备份需妥善保管。同一数据目录只允许一个进程运行。

### 常见问题

| 现象 | 检查方式 |
| --- | --- |
| `uv: command not found` | 完成 uv 安装后重新打开终端，按安装提示检查命令路径 |
| Python 或依赖下载失败 | 检查终端网络后重试 `uv python install 3.12` 或 `uv sync --locked` |
| 页面打不开 | 保持启动终端运行，确认出现 `Application startup complete`；检查浏览器端口与实际启动端口一致 |
| 8765 端口已占用 | 停止已有实例，或按“更换端口与可选配置”设置新端口并更新 QQ 上报地址 |
| 找不到令牌文件 | 确认服务已启动，并在项目目录读取 `.data/admin-token`；如使用自定义数据目录，以配置为准 |
| 令牌无效 | 核对正在访问的实例；配置了 `QECHO_ADMIN_TOKEN` 时，应使用该环境变量的值 |
| `No module named fcntl` | Windows 原生运行尚未支持，请使用当前支持的 macOS / Linux 环境 |
| AI 请求失败 | 检查 API 根地址、模型名称、密钥和账户状态；本机模型还需确认其服务已启动 |
| 桥接登录账号不一致 | QEcho 的“本人 QQ 号”必须与 NapCat 实际登录账号一致；更换账号前按[数据管理说明](docs/deployment.md)清理旧会话 |
| QQ 来信没有进入会话 | 检查双向地址、事件 token、本人 QQ 号、好友文本私聊范围、全局启用、联系人授权和接管状态 |
| 发送结果未知 | 先到 QQ 核对实际送达；系统不会自动重试未知结果 |

本地启动、鉴权、演练和自动化测试的验证范围见[验证记录](docs/validation.md)。真实模型的人格效果和真实 QQ 收发需在自己的账号环境继续验收。

## 文档

- [产品与架构设计](docs/user-controlled-agent-design.md)
- [实施计划](docs/user-controlled-agent-plan.md)
- [QQ 接入](docs/qq-connection.md)
- [部署与数据管理](docs/deployment.md)
- [接口说明](docs/api.md)
- [验证记录与限制](docs/validation.md)

## 开发

在已安装依赖的项目目录执行：

```sh
uv run pytest
uv run ruff check .
```

请勿把 `.data/`、`.env`、聊天记录和密钥提交到仓库。模型适配使用兼容 `/chat/completions` 的接口，QQ 适配使用外置 OneBot 11 桥接；SQLite 和前端无需额外服务或 Node 构建。

容器配置保留在 [Dockerfile](Dockerfile) 和 [compose.yaml](compose.yaml)，相关说明见[部署文档附录](docs/deployment.md#docker)。该路线尚未完成实际容器运行验收。
