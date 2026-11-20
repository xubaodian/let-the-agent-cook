# 安装和第一次文件任务

从 [Agent Cook Releases](https://github.com/xubaodian/let-the-agent-cook/releases/latest) 下载对应的安装包。CLI 和 Python 随安装包提供。

| 系统 | 安装包 | 安装后打开 |
| --- | --- | --- |
| Windows 11 x64 | `AgentCook-windows-x64.msi` | 双击安装，从开始菜单打开 Agent Cook。 |
| macOS Apple Silicon | `AgentCook-macos-arm64.pkg` | 双击安装，在「应用程序」打开 Agent Cook。 |
| Ubuntu 24.04 x64 | `AgentCook-ubuntu-amd64.deb` | 用软件安装器打开包，再从应用菜单打开 Agent Cook。也可从终端运行 `agent-cook`。 |

1. 打开 Agent Cook。按 Enter 在浏览器授权 OpenRouter，或输入 `key` 后粘贴已有 Key。选择对话模型，或者输入模型 ID。之后可通过 `/setup` 更新模型配置。
2. 输入 `/example` 生成样例目录。把终端给出的任务粘贴回 CLI。处理自己的文件时，可用 `@路径` 或 `@"带空格的目录"` 选择文件夹。多个输入可以一起选择。
3. 第一次文件任务会检查 Docker。如果需要安装或启动 Docker，完成提示中的操作后按 Enter，CLI 会继续同一任务。已安装的版本对应镜像会直接复用。 [Windows](https://docs.docker.com/desktop/setup/install/windows-install/) · [macOS](https://docs.docker.com/desktop/setup/install/mac-install/) · [Linux](https://docs.docker.com/engine/install/)
4. 成功后，终端会显示结果目录的绝对路径。输入 `/open` 可在文件管理器打开。安装包的应用入口将任务记录和结果保存在用户主目录的 `Agent Cook/.agent-cook/runs/`。

附加目录时保留目录名和文件层级，原件保留在宿主机。每个文件最多 16 MiB，一批输入最多 32 MiB。导入跳过目录内的符号链接与 Windows 联接。路径冲突会提示具体文件或文件夹。成功后 `<run_id>-results/` 包含通过最终校验的产物。提交批次与记录在同一层，供回放与续接使用。

终端里也可用 `agent-cook exec --input "资料 样例" "根据销售表生成简报"`。带输入时默认使用本机 Docker。用 `--sandbox docker` 发起无附件的生成任务。交互模式可以直接对话，按 `Ctrl+C` 可取消当前任务。

对话模型保存在用户配置。OpenRouter Key 保存在 Windows 凭据管理器、macOS 钥匙串或 Linux Secret Service。Ubuntu 桌面可用 GNOME Keyring 提供该服务。如果凭据存储暂不可用，CLI 会提示本次会话可使用 Key。命令行和环境变量仍可指定模型或 Key。非交互命令也可使用已保存的配置。

## 构建和发布

PR 的 [Actions](https://github.com/xubaodian/let-the-agent-cook/actions) 构建三端安装包，在对应的托管 runner 安装并运行入口。部署方可用仓库变量 `AGENT_COOK_MODELS_JSON` 以 [models.example.json](../models.example.json) 的格式向安装包提供公开候选模型。也可在本地打包时传 `--models models.json`。

维护者发布时使用与 `agent/pyproject.toml` 匹配的版本标签，例如 `v0.1.0`。发布流水线安装锁定的依赖，签署 Windows MSI，签署并公证 macOS PKG，并构建 Linux amd64 和 arm64 的配套镜像。首次发布需要把 GHCR 中的 `agent-cook-sandbox` 包设为公开，使安装后的第一次文件任务可直接拉取。流水线验证两个架构都可匿名读取后上传安装包。下载页源码位于 [docs/download/index.html](download/index.html)，可以随仓库的 `docs/` 部署。 [Windows 安装范围](https://learn.microsoft.com/en-us/windows/win32/msi/installation-context) · [Apple 公证](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution) · [多架构镜像](https://docs.docker.com/build/building/multi-platform/)

Windows 签名使用 `WINDOWS_PFX_BASE64` 和 `WINDOWS_PFX_PASSWORD`。macOS 使用 `MACOS_APP_IDENTITY`、`MACOS_INSTALLER_IDENTITY` 及两个身份对应的 `MACOS_APP_P12_BASE64`、`MACOS_INSTALLER_P12_BASE64` 和各自的 `*_P12_PASSWORD`。公证使用 `MACOS_NOTARY_KEY_BASE64`、`MACOS_NOTARY_KEY_ID` 和 `MACOS_NOTARY_ISSUER`。

## 首发验收

在干净的 Windows 11 x64、Apple Silicon Mac 和 Ubuntu 24.04 x64 上用对应安装包验证。

1. 从开始菜单、应用程序或应用菜单启动。完成 OpenRouter 连接，退出后重新打开，确认模型和凭据可以加载。
2. 在 Docker 未就绪时运行 `/example` 给出的任务，核对提示和重试。Docker 就绪后观察首次下载，再执行一次相同任务以检查镜像复用。
3. 用 `/open` 打开交付目录，对照包含中文、空格和子目录的样例，核对层级、处理后的副本和原始文件。以下脚本也可从已安装的 CLI 走完整路径，会调用已配置的 OpenRouter 模型。

| 系统 | 命令 |
| --- | --- |
| Windows PowerShell | `python installers/smoke.py "$env:LOCALAPPDATA/Programs/Agent Cook/agent-cook.exe" --docker` |
| macOS | `python3 installers/smoke.py "/Applications/Agent Cook.app/Contents/Resources/agent-cook/agent-cook" --docker` |
| Ubuntu | `python3 installers/smoke.py /usr/bin/agent-cook --docker` |

4. 从仓库运行以下回归和真实 Docker 测试。Docker 测试会在提交后让模型失败或取消，确认只保留回放批次，并检查同一路径重复提交时交付最新版本。首次发布前可传 `--docker-image agent-cook-sandbox:local` 让安装验收使用本地构建的镜像。

```bash
uv run --project agent --locked --all-extras python -m unittest discover -s agent/tests -v
docker build -f sandbox/Dockerfile -t agent-cook-sandbox:local .
AGENT_COOK_TEST_DOCKER_IMAGE=agent-cook-sandbox:local uv run --project agent --locked python -m unittest discover -s agent/tests -p test_docker_delivery.py -v
```

PowerShell 中先设置 `$env:AGENT_COOK_TEST_DOCKER_IMAGE = 'agent-cook-sandbox:local'`，再运行上面最后一行的 `uv run` 命令。
