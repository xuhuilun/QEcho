# 部署与数据管理

## 本机部署

默认采用 Python 3.12 + uv 源码本地运行，完整环境安装、登录、AI 配置和 QQ 验收步骤见 [README 本地部署](../README.md#本地部署)。进程锁在 Windows 使用标准库 `msvcrt`，其他平台使用 `fcntl`；测试结果见[验证记录](validation.md)。当前架构使用单进程会话锁，同一数据目录会通过操作系统文件锁拒绝第二个实例。不要使用多 worker 或同时运行多个实例处理同一 QQ 账号。

```sh
git clone https://github.com/xuhuilun/QEcho.git
cd QEcho
uv python install 3.12
```

先下载并安装项目的 Python 依赖：

```sh
uv sync --locked
uv run --no-sync python -c "import fastapi, httpx, uvicorn, qecho; print('Python 依赖安装成功')"
```

`uv sync --locked` 会创建或同步项目 `.venv`，按锁定版本安装 FastAPI、Uvicorn、HTTPX 及间接依赖，并安装 QEcho 和启动命令。当前配置默认包含开发依赖。检查输出 `Python 依赖安装成功` 后，再启动服务；无需手动激活虚拟环境：

```sh
uv run qecho
```

默认地址 `http://127.0.0.1:8765`。等待 `Application startup complete` 后，在另一个终端进入项目目录读取 `.data/admin-token` 并在页面登录（PowerShell：`Get-Content .data/admin-token`；macOS/Linux：`cat .data/admin-token`）。不要把令牌放在页面 URL 中。默认数据保存在宿主机项目的 `.data/`。终端启动日志不会输出令牌内容。默认配置是停止、演练模型、好友未授权。服务运行时保持启动终端打开，按 `Ctrl+C` 停止；再次执行启动命令恢复。

QEcho 与模型、NapCat 都在同一台电脑的同一系统环境直接运行时，使用各自的本机监听地址。NapCat 默认示例为 `http://127.0.0.1:3000`，事件上报地址为 `http://127.0.0.1:8765/webhooks/onebot`。跨主机、虚拟机或容器时需调整为实际可达地址。

可通过环境变量覆盖：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `QECHO_HOST` | `127.0.0.1` | 监听地址 |
| `QECHO_PORT` | `8765` | 监听端口 |
| `QECHO_DATA_DIR` | 当前工作目录的 `.data` | 数据、令牌和实例锁 |
| `QECHO_ADMIN_TOKEN` | 自动生成至 `admin-token` | 管理 API 和控制台访问 |
| `QECHO_WEBHOOK_TOKEN` | 自动生成至 `webhook-token` | 桥接事件认证，独立于管理令牌 |

如使用 `.env`：复制 `.env.example`，修改后运行 `uv run --env-file .env qecho`。普通 `uv run qecho` 不自动加载 `.env`。覆盖令牌时使用至少 16 个 ASCII 字符的随机值，管理令牌与事件令牌应不同。环境变量覆盖不会改写旧令牌文件，以当前环境变量为准。

例如将 `.env` 的 `QECHO_PORT` 改为 `8766` 后，浏览器与 NapCat 事件上报地址都需改用 `8766`。AI、QQ 和联系人的日常配置仍在工作台保存。

## Docker

容器路线保留为附录，尚未完成实际运行验收。以下命令从仓库根目录执行，要求 Docker 和 Compose 服务已安装并启动。默认本地部署使用前述源码流程；AI 和联系人配置操作见 README。

```sh
docker compose up -d --build
docker compose logs --tail=50 qecho
```

等待日志出现 `Application startup complete`，再读取管理令牌并在浏览器打开 `http://127.0.0.1:8765` 登录：

```sh
docker compose exec qecho cat /app/.data/admin-token
```

Compose 仅向宿主机回环地址发布 8765 端口，持久化到命名卷。容器以非 root 用户运行。NapCat 在宿主机时，QEcho 中桥接地址使用 `http://host.docker.internal:3000`，同时确保 NapCat 的监听地址可被容器访问；NapCat 上报地址使用宿主机的 `http://127.0.0.1:8765/webhooks/onebot`。NapCat 也在容器时应把两个服务接入同一专用网络，使用服务名访问，回环地址不跨容器。

Docker 数据目录为容器内的 `/app/.data`，不能通过读取宿主项目的 `.data/` 获取令牌。事件令牌通过 `docker compose exec qecho cat /app/.data/webhook-token` 读取。当前 Compose 不含 `env_file` 或 `environment` 配置，项目 `.env` 中的 `QECHO_*` 不会自动传入容器；默认通过控制台保存模型和 QQ 设置。如需覆盖容器环境，需明确修改 Compose 中的服务环境配置。

普通 `docker compose down` 保留命名卷；附加 `-v` 会删除卷中的配置和聊天数据。升级前在页面暂停，再执行 `docker compose stop qecho`；可用 `docker compose cp qecho:/app/.data ../qecho-backup` 把完整数据目录复制到仓库外，每次使用新的备份目录并妥善保管其中的密钥。随后执行 `git pull --ff-only` 和 `docker compose up -d --build`。

Docker 构建定义已提供；本次交付未运行 Docker 容器验收。本机 `uv.lock` 锁定开发与运行环境，Docker 镜像当前使用 `pyproject.toml` 的依赖版本范围安装，构建时会解析兼容版本。

## 数据与备份

联系人、提示词、配置、消息及模型/桥接密钥保存在数据目录的 SQLite 文件中。POSIX 目录权限为 0700，数据库和生成的令牌文件为 0600；Windows 依赖所在数据目录继承的 ACL，不能把 POSIX 模式位当作 Windows 的访问保证。模型/桥接密钥仅对管理 API 隐藏；认证主人可通过 `/api/setup` 获取配置消息上报所需的事件令牌。SQLite 本身未做静态加密，有数据目录访问权限的人可读取它。备份前先停止自动回复并停止服务，再备份整个数据目录。

管理页面可以删除联系人及其全部记录，或清理单个联系人的会话。删除不能恢复，不会删除 QQ 上的原消息。平台事件的去重标识会保留，避免清理后重新收到重复消息触发旧回复。

已有联系人的 QQ 号不能直接更换，需新建联系人；更换绑定 QQ 账号前需清理所有真实 QQ 会话，防止把旧账号历史用于新账号。

## 暂停和恢复

- 首页“停止”暂停全部后续发送；检测到本人回复时暂停对应好友，再次明确开始可恢复所选好友。
- 保存人格或全局配置会使已有草稿失效，可以对最新 QQ 来信重新生成，重新生成始终保留为草稿。
- 已经开始的桥接请求可能送达，暂停不等于撤回；请以 QQ 实际记录核对。
- 发送结果未知时不自动重试。服务重启把在途发送记为未知，把未完成生成/收件任务取消；不会重放自动发送。
- 自动识别本人回复依赖桥接的自身消息上报，始终可在控制台停止全部自动回复。

## 网络部署

默认作为本地个人工具使用。需要跨主机时，由部署者配置受控网络、HTTPS 反向代理和相应监听地址，保持两个方向的认证。不要直接将含聊天记录和管理接口的端口暴露到公网。本版本没有多人账户、权限分组或分布式任务队列。
