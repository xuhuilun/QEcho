# Windows 客户端

Windows 版提供 x64 EXE 安装包，内置 Python 和 QEcho 依赖。安装向导使用英文，客户端窗口使用中文。首次安装后从开始菜单打开 QEcho，点击「打开配置文件」填写模型与 QQ 连接。保持 NapCat 已安装并登录，设置 `agent.auto_start = true` 后启动服务。日常使用可直接打开客户端，配置和数据库默认保存在 `%LOCALAPPDATA%\QEcho`。[配置文件说明](file-configuration.md)

客户端启动时和运行中每 15 分钟检查 QEcho 正式发布。发现版本号更高的正式安装包时弹窗提示。用户确认后下载并校验，暂停服务、覆盖安装，再打开新版客户端。点击「启动服务」恢复代聊。关闭客户端期间，更新会在下次启动时检查。也可点击「检查更新」立即查询。[更新设计](windows-client-design.md)

## 构建与发布

在 Windows x64 安装 Python 3.12、uv、PowerShell 7.2+ 和 Inno Setup 6.5+，从仓库根目录运行：

```powershell
pwsh -File packaging/windows/build.ps1
```

预览包位于 `dist/windows/preview/<版本>/QEcho-Setup-<版本>-x64.exe`。Windows CI 产出同样的预览包，并在隔离运行器中测试签名和覆盖安装。预览包未签名，正式更新通道使用签名版本。

正式发布先提升 `pyproject.toml` 中的版本并更新锁文件，在对应提交建立 `v<版本>` 标签。签名脚本接收 `-FilePath`，使用项目的可信 Authenticode 代码签名证书对文件原位签名，并添加可信时间戳。签名凭据由发布者在构建环境管理。

```powershell
pwsh -File packaging/windows/build.ps1 -Release -SigningScript C:/signing/sign-qecho.ps1
```

将输出目录 `dist/windows/release/<版本>/` 中的 `QEcho-Setup-<版本>-x64.exe` 和 `windows-x64.json` 一起上传到对应 GitHub Release 的草稿，验证资产齐全后发布并标记为最新正式版本。已安装的签名客户端会检查并提示该版本。更新要求安装包与当前客户端的签名证书一致，证书轮换后用户需先手动安装一次由新证书签名的客户端。[GitHub Releases](https://github.com/xuhuilun/QEcho/releases)
