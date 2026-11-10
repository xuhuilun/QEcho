# QEcho · 回声

**登录 QQ，勾选好友，写好提示词，开始自动回复。**

QEcho 是一个本地运行的 QQ 代聊 Agent。你决定回复哪些好友，并用一份提示词控制人格、语气和聊天规则。界面只保留好友选择、提示词和开始/停止按钮，模型与 QQ 连接参数收在折叠设置中。

![QEcho 极简单页](docs/images/dashboard.png)

截图使用本地模拟登录、模型与桥接验收，不是真实 QQ 对话。

部署顺序：[启动 QEcho](#本地部署) → [安装并连接 NapCat](#napcat-部署与连接) → [开始代聊](#开始代聊)。

## 本地部署

采用 Python 3.12 + uv 源码运行。Windows / Linux 的依赖安装和自动化测试已通过 CI，macOS 已完成本地验收，具体范围见[验证记录](docs/validation.md)。QQ 登录由外置 NapCat 提供，按其支持的平台安装；一个 QEcho 数据目录对应一个 QQ 账号。

### 1. 安装 Git 和 uv

安装 [Git](https://git-scm.com/downloads)，再按 [uv 官方说明](https://docs.astral.sh/uv/getting-started/installation/)安装 uv。

Windows PowerShell：

```powershell
winget install --id=astral-sh.uv -e
```

macOS / Linux：

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

重新打开终端，确认 `git --version` 和 `uv --version` 可用。Python 由下一步的 uv 命令管理，前端无需 Node.js 构建。

### 2. 下载项目、安装 Python 和依赖

```sh
git clone https://github.com/xuhuilun/QEcho.git
cd QEcho
uv python install 3.12
```

**先下载并安装项目所需的 Python 依赖：**

```sh
uv sync --locked
```

该命令会创建项目的 `.venv`，按 `uv.lock` 安装 FastAPI、Uvicorn、HTTPX、间接依赖及 QEcho 启动命令。当前默认同时安装开发检查工具；已有下载缓存会复用。无需逐个安装包或手动激活虚拟环境。

检查安装结果：

```sh
uv run --no-sync python --version
uv run --no-sync python -c "import fastapi, httpx, uvicorn, qecho; print('Python dependencies ready')"
```

应显示 Python `3.12.x` 和 `Python dependencies ready`，再执行下一步。首次下载 Python 和依赖需要可用网络。

### 3. 启动并进入页面

在项目目录执行：

```sh
uv run qecho
```

看到 `Application startup complete` 后，保持终端运行，打开 <http://127.0.0.1:8765>。健康检查地址 <http://127.0.0.1:8765/healthz> 返回 `{"status":"ok"}`。

另开终端进入同一项目目录，读取管理令牌并填入登录页。

Windows PowerShell：

```powershell
Get-Content .data/admin-token
```

macOS / Linux：

```sh
cat .data/admin-token
```

`.data/` 保存数据库、令牌、提示词和聊天记录，默认已被 Git 忽略。管理令牌登录状态只保存在当前浏览器会话中。

### 4. 配置 AI 模型

展开页面底部的“连接设置”，将模型模式切换为“真实模型”，填写服务商的 API 根地址、模型名称与 API Key。QEcho 在根地址后追加 `/chat/completions`，读取 `choices[0].message.content`。密钥保存后不回显；无需鉴权的自部署模型可留空。演练模型不能用于开始真实自动回复。

接着按下面的步骤安装并连接 NapCat。

## NapCat 部署与连接

QEcho 负责 AI 和代聊规则，NapCat 负责 QQ 登录与消息收发。**两者都需要启动并保持运行。** 以下步骤默认两者在同一台电脑上直接运行。

### 1. 安装并启动 NapCat

根据自己的系统完成对应步骤。以下入口已于 2026-11-10 核对；安装包名称和系统要求变化时，以链接中的官方说明为准。

#### Windows

推荐官方一键安装包，适用 Windows amd64（通常为 Intel/AMD 64 位电脑）：

1. 打开 [NapCatQQ 官方下载页](https://github.com/NapNeko/NapCatQQ/releases/latest)，在 **Assets** 中下载 `NapCat.Shell.Windows.OneKey.zip` 并解压。
2. 运行解压后的 `NapCatInstaller.exe`，按提示完成安装。此安装方式无需预先安装 QQ。
3. 进入生成的 `NapCat.XXXX.Shell` 目录，双击 **`napcat.bat`** 启动。
4. 保持 NapCat 运行，记录启动窗口给出的 WebUI 地址，继续下一节登录。

这里的文件名对应官方 **OneKey** 安装包。[官方 Windows 一键安装说明](https://napneko.github.io/guide/boot/Shell#windows-onekey)

#### macOS

使用官方 [NapCat-Mac-Installer](https://github.com/NapNeko/NapCat-Mac-Installer)，要求 macOS 12+，支持 Intel / Apple Silicon：

1. 先将 QQ 安装到 **`/Applications/QQ.app`**。
2. 从[安装器官方下载页](https://github.com/NapNeko/NapCat-Mac-Installer/releases/latest)下载并打开安装器，选择 NapCat 版本及下载方式后安装。
3. 按提示备份并授权修改 QQ 入口，选择 **“切换程序入口 NapCat”**，再启动。
4. 如果系统拒绝修改入口，在“系统设置 → 隐私与安全性 → App 管理”允许安装器管理 QQ；首次要求访问 QQ 数据目录时，按安装器提示授权。
5. 保持 NapCat / QQ 运行，从启动输出获取 WebUI 地址。

恢复原版入口时，在安装器选择“切换程序入口 原版 QQ”。更新时保留原配置和插件。[官方 macOS 安装说明](https://napneko.github.io/guide/boot/Shell#macos)

#### Linux

推荐官方安装脚本，适用使用 apt / dnf 的 amd64、arm64 系统。在准备运行 NapCat 的用户终端中执行：

```sh
curl -fL --connect-timeout 20 --max-time 1800 \
  -o napcat.sh https://raw.githubusercontent.com/NapNeko/NapCat-Installer/main/script/install.sh &&
bash napcat.sh --docker n --cli y --github-proxy 0
```

该命令使用官方原生安装路线，默认安装到当前用户的 `~/Napcat`。完成后使用同一用户运行管理命令，按管理界面提示启动：

```sh
napcat
```

保持服务运行，查看启动日志中的 WebUI 地址。其他发行版、缺失系统依赖或下载问题按[官方 Linux 安装说明](https://napneko.github.io/guide/boot/Shell#linux)排查。

### 2. 打开 NapCat WebUI 并登录 QQ

1. 打开 NapCat 启动日志给出的 WebUI 地址，常见为 `http://127.0.0.1:6099/webui/`。
2. 使用 NapCat 日志中的 WebUI token / 密码进入管理页；需要时查看 NapCat 实际配置目录中的 `webui.json`。这是 **NapCat WebUI 登录凭据**。
3. 进入 QQ 登录页，选择扫码登录，用手机 QQ 扫码并确认，等待页面显示登录完成。如果页面要求重新认证或修改 WebUI 密码，按当前提示完成。
4. 确认实际登录的是准备交给 QEcho 代聊的 QQ。

WebUI 默认端口为 `6099`，被占用时可能自动递增，以启动日志为准。三个端口分别负责：

| 默认端口 | 用途 |
| --- | --- |
| `8765` | QEcho 页面、管理 API 和事件接收 |
| `6099` | NapCat WebUI，登录 QQ 与管理连接 |
| `3000` | 下面要创建的 NapCat OneBot HTTP 服务，供 QEcho 查询好友和发送消息 |

登录页地址与 WebUI 凭据行为见 [NapCat 官方配置说明](https://napneko.github.io/config/basic)。

### 3. 在 NapCat 创建 HTTP 服务端

在 NapCat WebUI 的网络配置中“新建”一个 **HTTP 服务端**，填写并保存启用：

| 字段 | 填写内容 |
| --- | --- |
| 名称 | `qecho-api` |
| 启用 / 保存时启用 | 开启 |
| Host / 监听地址 | `127.0.0.1` |
| Port / 端口 | `3000` |
| 消息格式 | `array`（数组） |
| Token / 访问令牌 | 自己生成的一段随机字符串，下一步原样填入 QEcho |

可以在 QEcho 项目目录执行以下命令生成随机字符串，然后保存到两边的对应字段：

```sh
uv run --no-sync python -c "import secrets; print(secrets.token_urlsafe(32))"
```

该 HTTP 服务用于 **QEcho → NapCat** 的查询与发送调用。

### 4. 把 NapCat 服务地址填入 QEcho

返回 QEcho，展开“连接设置 → QQ 连接设置”：

| QEcho 字段 | 填写内容 |
| --- | --- |
| NapCat / OneBot HTTP 服务地址 | `http://127.0.0.1:3000` |
| 桥接访问令牌 | 上一步 HTTP 服务端设置的 token |
| QQ 登录页地址 | `http://127.0.0.1:6099/webui/qq_login`，端口按实际 WebUI 地址修改 |

点击“保存连接设置”。保存会暂停代聊；首次连接时保持停止即可。本人 QQ 号由同步自动识别，无需手填。

### 5. 在 NapCat 创建 HTTP 客户端

在 QEcho 的“QQ 连接设置”里复制 **上报地址** 和 **事件令牌**。返回 NapCat 网络配置，新建一个 **HTTP 客户端**，填写并保存启用：

| 字段 | 填写内容 |
| --- | --- |
| 名称 | `qecho-events` |
| 启用 / 保存时启用 | 开启 |
| URL / 上报地址 | QEcho 提供的地址，默认 `http://127.0.0.1:8765/webhooks/onebot` |
| 消息格式 | `array`（数组） |
| `reportSelfMessage` / 上报自身消息 | 开启，用于识别本人回复并接管 |
| Token | QEcho 页面显示的 **事件令牌** |

该连接用于 **NapCat → QEcho** 推送好友消息。NapCat 会使用事件令牌对上报正文签名，QEcho 验证后接收。完整字段依据见 [NapCat 官方网络配置](https://napneko.github.io/config/basic)。

四种令牌用途如下：

| 名称 | 从哪里取得 | 填到哪里 |
| --- | --- | --- |
| QEcho 管理令牌 | QEcho 的 `.data/admin-token` | QEcho 首次进入页面的登录框 |
| NapCat WebUI token / 密码 | NapCat 启动日志或实际配置目录 | NapCat WebUI 登录页 |
| NapCat HTTP 服务端 token | 上述第 3 步自己设置 | QEcho 的“桥接访问令牌” |
| QEcho 事件令牌 | QEcho“QQ 连接设置”中复制 | NapCat HTTP 客户端的 token |

### 6. 检查连接是否成功

1. 保持 QEcho 和 NapCat 都在运行，在 QEcho 点击“同步好友”。出现正确的 QQ 号与好友列表，说明查询方向正常。
2. 勾选一位测试好友，填写统一提示词，并确认模型已配置为真实模型，点击“保存并开始自动回复”。
3. 让这位好友发来文本私聊，检查“最近运行状态”和 QQ 实际收到的回复，确认事件上报与发送都正常。
4. 点击“停止”，再确认后续来信不自动回复。通过后按需增加好友。

常见连接问题：

| 现象 | 排查 |
| --- | --- |
| NapCat 登录页打不开 | 先启动 NapCat；按其日志确认 WebUI 实际端口，更新 QEcho 登录页地址 |
| 已登录 QQ，但同步好友失败 | 检查 HTTP 服务端是否已启用、3000 端口和服务端 token 是否一致 |
| 好友能同步，但没有回复 | 检查 HTTP 客户端已启用、上报 URL 和事件令牌正确，以及 QEcho 已开始且该好友被勾选 |
| 401 / 签名校验失败 | 按上面的令牌表核对两边对应值；修改事件令牌后同步更新 NapCat HTTP 客户端 |
| 本人回复后 Agent 仍在回复 | 检查 `reportSelfMessage` 已开启且桥接实际报告本人消息；仍可在 QEcho 点击“停止” |

以上地址只适用于同一电脑、同一网络环境。跨电脑、虚拟机或容器时，`127.0.0.1` 指各自环境自身，应改成双方可达的地址并保留认证、限制访问范围，详见 [QQ 接入说明](docs/qq-connection.md)。安装说明和字段已按官方资料核对，真实账号登录与收发仍需在使用者环境完成验收。

## 开始代聊

1. 点击 **登录 QQ**，打开 NapCat 登录页。页面可能先要求 NapCat WebUI 认证，再选择扫码登录，并在手机确认。
2. 返回 QEcho，登录成功后自动读取 QQ 账号和好友；必要时点击 **同步好友**。无需手填本人 QQ 号。
3. 搜索并勾选需要代聊的好友，在右侧写一份统一 Agent 提示词，例如：

```text
像平时的我一样聊天，语气温柔自然，回复简短。
认真回应对方提到的事情，不要连续追问。
可以聊音乐、电影和日常，不知道我的经历或安排时不要编造。
```

4. 点击 **保存并开始自动回复**。只对选中好友的文本私聊自动回复，未选择的好友和群聊不参与。页面显示运行状态与最多 8 条最近回复状态。

修改选择或提示词后，需再次点击“保存并开始自动回复”一起生效；未应用的修改会有提示。需要立即暂停时，点击始终可用的 **停止**。本人主动回复可接管单个好友，再次明确开始可恢复所选好友。

开始时会重新核对实际 QQ 账号、好友范围和配置版本，失败保持停止。已有真实聊天历史时检测到换号，会暂停并要求新账号使用独立数据目录。已开始的发送请求可能仍会送达，停止不等于撤回；发送结果未知时系统不自动重试。

## 更换端口、备份与升级

默认配置无需 `.env`。如需更换端口，先按 `Ctrl+C` 停止服务，再复制配置并编辑 `QECHO_PORT`：

```sh
cp .env.example .env
uv run --env-file .env qecho
```

Windows PowerShell 复制文件可用 `Copy-Item .env.example .env`。例如改用 8766 端口后，浏览器和 NapCat 上报 URL 都同步改成 8766。普通 `uv run qecho` 不自动加载 `.env`；恢复服务时继续使用与你的配置一致的启动命令。

升级前在页面停止自动回复，再在运行终端按 `Ctrl+C` 停止服务，将整个 `.data/` 复制到仓库外的新备份目录。随后更新代码、重新安装锁定依赖并启动：

```sh
git pull --ff-only
uv sync --locked
uv run qecho
```

使用 `.env` 时，最后一行替换为 `uv run --env-file .env qecho`。数据目录含密钥和聊天记录，备份需妥善保管。数据库未做静态加密；POSIX 文件模式限制和 Windows 继承的 ACL 行为见[部署与数据管理](docs/deployment.md)。

## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| `uv` 命令不存在 | 安装后重新打开终端，按安装提示检查 PATH |
| Python / 依赖安装失败 | 检查网络后重试 `uv python install 3.12` / `uv sync --locked`，保留完整安装报错 |
| 页面打不开 | 保持服务终端运行，确认启动成功且端口正确 |
| 令牌文件不存在或无效 | 先启动服务，在项目目录读取；自定义数据目录或环境令牌时以实际配置为准 |
| 数据目录正在使用 | 停止同目录已有实例；同一个数据目录只允许一个进程 |
| 登录页打不开 | 先启动 NapCat，检查折叠设置中的登录页地址和实际 WebUI 端口 |
| 登录后没有好友 | 检查 OneBot HTTP 服务是否启用、地址/token 是否正确，然后同步好友 |
| 开始时报配置变化 | 其他操作或人工接管发生在启动期间；查看当前状态，再明确开始 |
| 好友来信不回复 | 检查运行开关、选中范围、真人接管状态以及最近错误；只支持好友文本私聊 |
| AI 请求失败 | 检查模型地址、名称、密钥和服务账户状态 |

## 文档与开发

- [极简流程设计](docs/simple-automation-design.md)
- [实施计划](docs/simple-automation-plan.md)
- [QQ 接入](docs/qq-connection.md)
- [部署与数据管理](docs/deployment.md)
- [API 说明](docs/api.md)
- [验证记录与限制](docs/validation.md)

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check qecho tests
```

原有会话 API 和联系人数据保留兼容；新的统一提示词模式不叠加旧联系人隐藏的人格字段。真实模型人格效果、QQ 登录与真实好友收发仍需用户环境验收。

[Dockerfile](Dockerfile) 与 [compose.yaml](compose.yaml) 保留为可选配置，尚未实际验收容器运行。
