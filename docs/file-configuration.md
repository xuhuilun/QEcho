# 配置文件与一键启动

推荐将 `qecho.example.toml` 复制成仓库根目录的 `qecho.toml`。它包含运行所需的模型、QQ 连接、好友范围、提示词和自动启动选项，不需要再在网页保存。安装 uv 和外置 NapCat 的步骤见 [README](../README.md#本地部署)。

## 启动顺序

1. 安装 uv，下载项目，将样例复制为 `qecho.toml`，用 UTF-8 文本编辑器修改。支持 Windows 编辑器保存的 UTF-8 BOM。
2. 在 NapCat 扫码登录，启用 OneBot HTTP 服务端与 HTTP 客户端。两边的地址和令牌按 [README 连接步骤](../README.md#napcat-部署与连接)填写。
3. 填好真实模型、好友 QQ 号和提示词，将 `agent.auto_start` 改为 `true`。
4. Windows 双击 `start.bat`；macOS 双击 `start.command`；Linux 执行 `./start.sh`。脚本固定进入自身目录，支持目录名含空格。

脚本执行 `uv python install 3.12`、`uv sync --locked --no-dev`，安装成功后通过 `uv run --no-sync qecho --config <绝对路径>` 启动。脚本不会安装 uv、NapCat 或代替用户扫码。没有真实配置时，仅复制样例并退出，编辑后需再次运行。安装或启动失败会中止，不继续后面的步骤。首次下载需要网络，之后复用缓存。

程序完整校验文件后才创建数据目录和打开数据库。启动成功后，后台等待 QQ 桥接和登录，确认实际登录账号和好友列表，再原子启用文件中的全部好友。错误好友名单、旧账号历史冲突等问题保持停止。`auto_start = false` 仅启动状态页面；文件模式没有网页开始按钮，需修改文件并重启。

## 字段

未填写的字段采用下列默认值；未知字段拒绝。字段类型严格校验，布尔值用 `true` / `false`，数字不要加引号，QQ 号必须加引号。

| 分组 | 字段 | 默认值与作用 |
| --- | --- | --- |
| server | `host` | `127.0.0.1`，QEcho 监听地址 |
| server | `port` | `8765`，范围 1–65535；更改后同步修改浏览器和 NapCat 事件上报 URL |
| server | `data_dir` | `.data`，相对路径以配置文件所在目录为基准，一个数据目录对应一个 QQ 账号 |
| server | `admin_token` | 空字符串，自动生成/读取数据目录的 `admin-token`；非空时至少 16 个 ASCII 字符 |
| model | `mode` | `live`；自动启动只允许真实模型，`demo` 仅演练 |
| model | `base_url` | 空字符串；聊天补全 API 根地址，程序追加 `/chat/completions` |
| model | `name` | 空字符串，服务商实际提供的模型名称 |
| model | `api_key` | 空字符串；无需鉴权的自部署模型可留空 |
| model | `temperature` | `0.7`，范围 0–2 |
| model | `context_messages` | `30`，范围 2–100 |
| model | `max_reply_chars` | `800`，范围 20–10000；超长回复会失败，不截断发送 |
| qq | `api_url` | `http://127.0.0.1:3000`，NapCat OneBot HTTP 服务端地址 |
| qq | `api_token` | 空字符串，与 NapCat HTTP 服务端 token 相同 |
| qq | `login_url` | `http://127.0.0.1:6099/webui/qq_login`，网页“登录 QQ”打开的地址，端口以 NapCat 日志为准 |
| qq | `event_token` | 空字符串，自动生成/读取数据目录的 `webhook-token`；非空时至少 16 个 ASCII 字符，与 NapCat HTTP 客户端 token 相同 |
| agent | `friends` | `[]`，最多 500 个不重复好友 QQ 号字符串，例如 `["12345678"]`；须为当前账号实际好友 |
| agent | `prompt` | 内置默认规则；可用 TOML 多行字符串，最长 20000 字符 |
| agent | `auto_start` | `false`；设为 `true` 后要求真实模型地址/名称、非空好友名单和提示词 |
| agent | `reply_delay_seconds` | `2.0`，范围 0–60，自动回复前等待秒数 |

模型与桥接 API 地址使用 HTTP/HTTPS，不含用户名、密码、查询参数或片段。模型请求兼容聊天补全接口，读取 `choices[0].message.content`；配置检查不探测模型服务是否可用。

`model.api_key` 与 `qq.api_token` 留空会**清除数据库中的旧密钥**，不会复用上次网页填写的值。管理/事件令牌留空则读取或生成令牌文件；非空配置优先于旧令牌文件，旧文件不会被改写。不要混用管理令牌、NapCat WebUI 密码、HTTP 服务端 token 与事件 token。

四类密钥/令牌只接受可打印 ASCII，不得含换行、Tab 或其他控制字符；复制时可去除两端普通空格。管理与事件令牌非空时还要求至少 16 字符。校验错误不会显示原值。

## 入口和优先级

```sh
uv run qecho --config qecho.toml --check-config
uv run qecho --config qecho.toml
```

配置路径顺序为 `--config PATH`、`QECHO_CONFIG`、当前目录的 `qecho.toml`。文件模式下，监听地址、端口、数据目录、管理与事件令牌均以文件为准，覆盖旧 `QECHO_HOST/PORT/DATA_DIR/ADMIN_TOKEN/WEBHOOK_TOKEN`。读取失败或字段错误退出，不退回旧数据库配置。

`--check-config` 只验证语法、类型和必填关系，不创建数据库、不登录 QQ、不打印密钥。命令仍需要先安装 Python 依赖。只有未指定配置路径且当前目录没有 `qecho.toml` 时，才沿用旧环境变量和网页模式。

## 修改、停止和备份

配置在每次启动时读取，不会热重载。导入文件时先暂停、取消旧未发送任务并停用旧授权，再按当前文件启动；保留联系人、消息历史和 QQ 账号绑定。若新账号与已有 QQ 历史不一致，应换用独立 `server.data_dir`。

文件模式网页显示配置文件名、目标名单、提示词、启动状态和最近回复；可以打开扫码页、同步好友和停止。网页和管理 API 均不能修改配置/名单或调用手动发送、重新生成等写操作。停止不等待登录或模型，且永久取消本轮后台启动，之后同步好友不会恢复自动回复。恢复需关闭进程，再次执行启动脚本。本人主动回复接管单个好友后，也需要明确重启才能按文件名单恢复。

停止不能撤回已经发出的请求，发送结果未知时不自动重试。重启只重新授权后续来信，不重放旧回复。

`qecho.toml` 已被 Git 和 Docker 构建上下文忽略，其中的模型密钥与令牌仍以明文存储。POSIX 启动器首次创建文件时使用 0600；Windows 使用目录继承的 ACL。不要提交、截图或分享真实文件。备份前先停止服务，备份配置文件和完整数据目录，保存在仓库外。

## 常见错误

| 情况 | 处理 |
| --- | --- |
| TOML 语法错误 | 检查英文引号、方括号和多行提示词结尾，使用 UTF-8 保存 |
| `agent.friends` 类型错误 | 使用 `friends = ["12345678"]`，不使用数字数组 |
| `requires_live_model` | 填写真实模型地址和名称，`mode = "live"` |
| 页面一直等待 | 检查 NapCat 是否启动/登录、服务是否暂时不可用；HTTP 服务端地址以实际配置为准 |
| 提示桥接配置错误 | 核对 API 地址、端口、token；HTTP 401/403/404 等明确配置错误会停止，不一直重试 |
| 页面显示启动失败 | 核对好友是否属于当前 QQ、实际登录账号、模型配置；修改后重启 |
| 点击停止后没有恢复 | 这是停止的约定，关闭当前进程并重新启动 |
| 文件改了但没有生效 | 完整重启；确认启动的是该配置路径且没有旧实例占用数据目录 |

实现边界与验证结果见[验证记录](validation.md)，设计见[集中配置设计](file-config-design.md)。
