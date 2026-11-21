# 安装和第一次文件任务

## 从源码安装

解压源码，在 macOS 或 Linux 运行 `./install`，在 Windows 双击 `install.cmd`。安装器优先使用兼容的 uv，必要时将固定版本装到源码自己的工具目录，再按 `agent/.python-version` 和 `agent/uv.lock` 安装 Agent Cook。安装完成会打开 CLI。以后用 `./cook` 或 `cook.cmd` 启动，重复运行安装可补齐依赖。[uv 安装器](https://docs.astral.sh/uv/reference/installer/)

## 从原生安装包安装

从 [Agent Cook Releases](https://github.com/xubaodian/let-the-agent-cook/releases/latest) 下载对应的安装包。CLI 和 Python 随安装包提供，打开后使用与源码安装相同的向导。

| 系统 | 安装包 | 安装后打开 |
| --- | --- | --- |
| Windows 11 x64 | `AgentCook-windows-x64.msi` | 双击安装，从开始菜单打开 Agent Cook。 |
| macOS Apple Silicon | `AgentCook-macos-arm64.pkg` | 双击安装，在「应用程序」打开 Agent Cook。 |
| Ubuntu 24.04 x64 | `AgentCook-ubuntu-amd64.deb` | 用软件安装器打开包，再从应用菜单打开 Agent Cook。也可从终端运行 `agent-cook`。 |

## 第一次文件任务

1. 打开 Agent Cook。按 Enter 在浏览器授权 OpenRouter，或输入 `key` 无回显粘贴已有 Key。可录入 Exa Key 或按 Enter 跳过，然后选择对话模型或输入模型 ID。
2. 输入 `/example` 生成样例目录。把终端给出的任务粘贴回 CLI。处理自己的文件时，可用 `@路径` 或 `@"带空格的目录"` 选择文件夹。多个输入可以一起选择。
3. 第一次文件任务会检查本机 Docker，包括使用 Docker 上下文的 Colima。按 Enter 启动已安装的引擎或打开官方安装说明，也可随时输入 `/docker`。处理完系统授权或重启提示后可重试同一任务。引擎就绪后，安装包会拉取配套镜像，源码会从现有 Dockerfile 构建本地镜像，已有镜像直接复用。[Windows](https://docs.docker.com/desktop/setup/install/windows-install/) · [macOS](https://docs.docker.com/desktop/setup/install/mac-install/) · [Linux](https://docs.docker.com/engine/install/)
4. 成功后，终端会显示结果目录的绝对路径。输入 `/open` 可在文件管理器打开。安装包的应用入口将任务记录和结果保存在用户主目录的 `Agent Cook/.agent-cook/runs/`。

附加目录时保留目录名和文件层级，原件保留在宿主机。每个文件最多 16 MiB，一批输入最多 32 MiB。导入跳过目录内的符号链接与 Windows 联接。路径冲突会提示具体文件或文件夹。成功后 `<run_id>-results/` 包含通过最终校验的产物。提交批次与记录在同一层，供回放与续接使用。

终端里也可用 `agent-cook exec --input "资料 样例" "根据销售表生成简报"`。带输入时默认使用本机 Docker。用 `--sandbox docker` 发起无附件的生成任务。交互模式可以直接对话，按 `Ctrl+C` 可取消当前任务。

## 配置

向导和命令把两把 Key 和模型保存到同一份 `agent-cook.json`。Agent Cook 写入时限制文件访问权限，只更新用户修改的字段。默认模型应在对应候选列表中。Exa 可留 `null`。[完整配置示例](../agent-cook.example.json)

| 系统 | 默认配置文件 |
| --- | --- |
| Windows | `%APPDATA%\Agent Cook\agent-cook.json` |
| macOS | `~/Library/Application Support/Agent Cook/agent-cook.json` |
| Linux | `$XDG_CONFIG_HOME/agent-cook/agent-cook.json`，未设置时使用 `~/.config/agent-cook/agent-cook.json` |

| CLI 命令 | 操作 |
| --- | --- |
| `/setup` | 连接 OpenRouter、更新可选的 Exa Key 并选择默认模型。 |
| `/key`、`/key openrouter`、`/key exa` | 无回显更换指定的 Key，下一条任务生效。OpenRouter 校验失败时保留原值。 |
| `/model`、`/model <ID>` | 查看当前模型及候选，或切换并保存模型。 |
| `/config` | 显示配置路径、当前模型和 Key 的配置来源。 |

用 `--config /path/to/agent-cook.json` 显式使用另一份配置。原有环境变量仍可覆盖配置，其来源会在 `/config` 和 `/key` 中显示。已有 OpenRouter 系统凭据和旧版 `settings.json` 仍可读取。手动编辑后重启即可加载更改。

## 构建和发布

PR 的 [Actions](https://github.com/xubaodian/let-the-agent-cook/actions) 构建三端安装包，在对应的托管 runner 安装并运行入口。部署方可用仓库变量 `AGENT_COOK_MODELS_JSON` 以 [models.example.json](../models.example.json) 的格式向安装包提供公开候选模型。也可在本地打包时传 `--models models.json`。

维护者发布时使用与 `agent/pyproject.toml` 匹配的版本标签，例如 `v0.1.0`。发布流水线安装锁定的依赖，签署 Windows MSI，签署并公证 macOS PKG，并构建 Linux amd64 和 arm64 的配套镜像。首次发布需要把 GHCR 中的 `agent-cook-sandbox` 包设为公开，使安装后的第一次文件任务可直接拉取。流水线验证两个架构都可匿名读取后上传安装包。下载页源码位于 [docs/download/index.html](download/index.html)，可以随仓库的 `docs/` 部署。 [Windows 安装范围](https://learn.microsoft.com/en-us/windows/win32/msi/installation-context) · [Apple 公证](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution) · [多架构镜像](https://docs.docker.com/build/building/multi-platform/)

Windows 签名使用 `WINDOWS_PFX_BASE64` 和 `WINDOWS_PFX_PASSWORD`。macOS 使用 `MACOS_APP_IDENTITY`、`MACOS_INSTALLER_IDENTITY` 及两个身份对应的 `MACOS_APP_P12_BASE64`、`MACOS_INSTALLER_P12_BASE64` 和各自的 `*_P12_PASSWORD`。公证使用 `MACOS_NOTARY_KEY_BASE64`、`MACOS_NOTARY_KEY_ID` 和 `MACOS_NOTARY_ISSUER`。

## 首发验收

在干净的 Windows 11 x64、Apple Silicon Mac 和 Ubuntu 24.04 x64 上用对应安装包验证。

1. 从安装脚本、开始菜单、应用程序或应用菜单启动。验证预填两把 Key 后可直接进入 CLI，也可在向导跳过 Exa。用 `/key exa` 更新 Exa，重启或重复安装后确认模型和 OpenRouter Key 仍能加载。
2. 在 Docker 未就绪时运行 `/example` 给出的任务，核对提示和重试。Docker 就绪后观察首次下载或构建，再执行一次相同任务以检查镜像复用。
3. 用 `/open` 打开交付目录，对照包含中文、空格和子目录的样例，核对层级、处理后的副本和原始文件。以下脚本也可从已安装的 CLI 走完整路径，会调用已配置的 OpenRouter 模型。

| 系统 | 命令 |
| --- | --- |
| Windows PowerShell | `python installers/smoke.py "$env:LOCALAPPDATA/Programs/Agent Cook/agent-cook.exe" --docker` |
| macOS | `python3 installers/smoke.py "/Applications/Agent Cook.app/Contents/Resources/agent-cook/agent-cook" --docker` |
| Ubuntu | `python3 installers/smoke.py /usr/bin/agent-cook --docker` |

4. 从仓库运行以下回归和真实 Docker 测试。Docker 测试会检查首次准备镜像、`/open` 的目标、任务失败或取消后的记录，以及同一路径重复提交时交付的最终版本。首次发布前可传 `--docker-image agent-cook-sandbox:local` 让安装验收使用本地镜像。

```bash
uv run --project agent --locked --all-extras python -m unittest discover -s agent/tests -v
AGENT_COOK_TEST_DOCKER_IMAGE=agent-cook-sandbox:local uv run --project agent --locked python -m unittest discover -s agent/tests -p test_docker_delivery.py -v
```

PowerShell 中先设置 `$env:AGENT_COOK_TEST_DOCKER_IMAGE = 'agent-cook-sandbox:local'`，再运行上面最后一行的 `uv run` 命令。
