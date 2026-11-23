# 安装和第一次文件任务

## 从源码安装

解压源码后进入 `agent/`。在 macOS 或 Linux 运行 `./install`，在 Windows 双击 `install.cmd`。安装器优先使用兼容的 uv，必要时将固定版本装到项目自己的工具目录，再按 `.python-version` 和 `uv.lock` 安装 Agent Cook。安装完成会打开 CLI。以后用 `./cook` 或 `cook.cmd` 启动，重复运行安装可补齐依赖。[uv 安装器](https://docs.astral.sh/uv/reference/installer/)

安装会准备工作区 Python 依赖、Node.js、npm、Playwright Chromium 和 LibreOffice。这些组件均通过准备后，安装才完成。LibreOffice 用于把 PPT 渲染成真实预览，已安装时自动复用。安装器在 macOS 使用 Homebrew，在 Windows 使用 WinGet，在 Ubuntu 使用 APT。系统软件包管理器不可用时，可以从官网下载 LibreOffice 并重新运行安装。 [预装依赖与 PPT 预览](workspace-tools.md#办公与数据处理依赖) · [LibreOffice 安装说明](https://www.libreoffice.org/installation-instructions/)

源码路径可以包含空格和中文。从其他目录调用时，给脚本路径加引号，例如 `bash "$HOME/Downloads/Agent Cook/agent/install"`。ZIP 解压后若执行权限丢失，可在 `agent/` 下用 `bash ./install` 安装、用 `bash ./cook` 启动。移动源码目录后，在新位置重新运行安装即可重建虚拟环境。

## 全局安装与更新

在新版源码的 `agent/` 目录执行 `./install --global`（macOS / Linux）或 `.\install.cmd --global`（Windows）。安装器在用户目录准备独立的 CLI，并尝试把命令入口加入用户 PATH。安装后打开新终端，从要处理文件的目录运行 `agent-cook`。已经保存的 `agent-cook.json` 会继续读取。源码目录已有的 `.env` 在首次全局安装时复制一份到版本目录的上一级，后续版本共用该文件。 [uv PATH 设置](https://docs.astral.sh/uv/reference/cli/#uv-tool-update-shell)

如果当前终端尚未读到新的 PATH，可以直接复制安装结束时 `Start now with` 后的命令启动。

| 系统 | 全局源码安装目录 |
| --- | --- |
| macOS | `~/Library/Application Support/Agent Cook/cli` |
| Linux | `$XDG_DATA_HOME/agent-cook/cli`，未设置时使用 `~/.local/share/agent-cook/cli` |
| Windows | `%LOCALAPPDATA%\Agent Cook\cli` |

| 交互命令 | 终端等价命令 | 用途 |
| --- | --- | --- |
| `/update` | `agent-cook update` | 安装 GitHub `main` 的最新提交，下次启动生效。 |
| `/update --check` | `agent-cook update --check` | 查看已装版本和 GitHub 最新提交。 |
| `/update --rollback` | `agent-cook update --rollback` | 下次启动恢复上一版。 |

更新用提交 SHA 下载源码，在独立目录按新版锁文件准备环境，再用当前配置试启动。若目标版本无法读取配置，终端会显示诊断并继续使用当前版本。可以调整报错字段或准备一份兼容配置，运行 `agent-cook update --config 路径` 重试，并用同一配置启动。更新过程保留原配置文件、凭据和会话。日常保留当前版和上一版，准备新版时最多同时有三个版本。旧进程仍占用第三个版本时，等它退出后再更新。

原生安装包通过 GitHub Releases 安装新版本。 [Agent Cook Releases](https://github.com/xubaodian/let-the-agent-cook/releases/latest)

## 从原生安装包安装

从 [Agent Cook Releases](https://github.com/xubaodian/let-the-agent-cook/releases/latest) 下载对应的安装包。CLI 和 Python 随安装包提供，打开后使用与源码安装相同的向导。

首次从应用菜单打开原生安装版时会准备工作区运行时。也可在终端用安装目录中的 CLI 执行 `setup-runtime`，补齐包括 LibreOffice 在内的组件。Ubuntu 安装包由 APT 安装 LibreOffice Impress 和中日韩字体。

| 系统 | 安装包 | 安装后打开 |
| --- | --- | --- |
| Windows 11 x64 | `AgentCook-windows-x64.msi` | 双击安装，从开始菜单打开 Agent Cook。 |
| macOS Apple Silicon | `AgentCook-macos-arm64.pkg` | 双击安装，在「应用程序」打开 Agent Cook。 |
| Ubuntu 24.04 x64 | `AgentCook-ubuntu-amd64.deb` | 用软件安装器打开包，再从应用菜单打开 Agent Cook。也可从终端运行 `agent-cook`。 |

## 第一次文件任务

1. 打开 Agent Cook。按 Enter 在浏览器授权 OpenRouter，或输入 `key` 无回显粘贴已有 Key。可录入 Exa Key 或按 Enter 跳过，然后选择对话模型或输入模型 ID。
2. 输入 `/example` 生成样例目录。把终端给出的任务粘贴回 CLI。处理自己的文件时，可用 `@路径` 或 `@"带空格的目录"` 选择文件夹。多个输入可以一起选择。
3. 本地脚本通过系统沙箱执行。Linux 源码用户需安装 Bubblewrap，例如在 Ubuntu 运行 `sudo apt install bubblewrap`。用 `agent-cook doctor --runtime` 可验证 Python、Node.js、npm、Chromium 和 LibreOffice。
4. 成功后，终端会显示结果目录的绝对路径。输入 `/open` 可在文件管理器打开。

附加目录时保留目录名和文件层级，原件保留在宿主机。每个文件最多 16 MiB，一批输入最多 32 MiB。导入跳过目录内的符号链接与 Windows 联接。路径冲突会提示具体文件或文件夹。成功后 `<run_id>-results/` 包含通过最终校验的产物。提交批次与记录在同一层，供回放与续接使用。

终端里也可用 `agent-cook exec --input "资料 样例" "根据销售表生成简报"`。CLI 默认用原生沙箱处理文件，支持无附件的生成任务。输入 `agent-cook doctor` 检查本机隔离是否可用。交互模式可以直接对话，按 `Ctrl+C` 可取消当前任务。

Ubuntu 24.04 的源码安装若报告 Bubblewrap 无法创建用户或网络命名空间，先安装 `apparmor-profiles`，检查系统当前加载的 Bubblewrap 策略。没有现有策略时，可安装并加载 Ubuntu 提供的单程序策略。DEB 安装包会准备这项依赖。 [Ubuntu AppArmor 说明](https://ubuntu.com/server/docs/security-apparmor/)

```bash
sudo apt install bubblewrap apparmor-profiles
sudo install -m 0644 /usr/share/apparmor/extra-profiles/bwrap-userns-restrict /etc/apparmor.d/bwrap-userns-restrict
sudo apparmor_parser -a /etc/apparmor.d/bwrap-userns-restrict
./cook doctor --runtime
```

## 输入路径与结果归档

`@路径` 的补全、相对路径和 `--input` 都以启动命令的目录为准。也可以用 `agent-cook -C 路径` 指定输入目录。文件会复制到本次任务的沙箱，最终产物和日志按会话归档到公共工作区。工作区默认是用户主目录下的 `Agent Cook/workspace/`。

| 路径 | 内容 |
| --- | --- |
| `日期/会话 ID/<run_id>-results/` | 本次任务最终交付的文件。 |
| `日期/会话 ID/<run_id>.jsonl` | 运行日志，可用于回放。 |
| `日期/会话 ID/<run_id>.model.ndjson` | 模型请求和原始响应，以及工具实际收到的参数和返回结果。 |
| `日期/会话 ID/<run_id>-artifacts/` | 各次提交的产物副本。 |

日期取会话建立时的本地日期，续接时沿用同一个会话目录。一次 `exec` 单独归档。在同一个输入目录运行 `agent-cook resume --last` 会继续该目录的最近会话。旧版在输入目录保存过会话时，CLI 会把会话索引复制到公共工作区。已有的结果可从原路径打开。显式指定 `--trace` 时，该次运行的产物归档在日志旁边。

CLI 默认记录模型诊断，终端通过 `Model trace` 显示文件路径。每次 HTTP 请求和响应可按 `request_id` 配对，工具输入和输出可按 `tool_call_id` 配对。使用 `agent-cook --no-model-trace`、`agent-cook exec --no-model-trace` 或 `agent-cook resume --last --no-model-trace` 可跳过诊断记录。记录保存在本地，包含任务正文及模型看到的内容。分享问题时可摘取相关记录。

可用 `agent-cook.json` 的 `workspace_dir` 指定其他工作区，或用环境变量 `AGENT_COOK_WORKSPACE` 覆盖。相对工作区路径以配置文件所在目录为准。`/config` 显示当前工作区的绝对路径。

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
| `/config` | 显示配置与工作区路径、当前模型和 Key 的配置来源。 |

用 `--config /path/to/agent-cook.json` 显式使用另一份配置。原有环境变量仍可覆盖配置，其来源会在 `/config` 和 `/key` 中显示。已有 OpenRouter 系统凭据和旧版 `settings.json` 仍可读取。手动编辑后重启即可加载更改。

每个任务默认最多 300 轮模型调用。可用 `--max-turns 500` 或在配置文件中设置 `run.max_turns` 调整，命令行参数优先。已有配置中的轮次上限继续生效，删除 `run.max_turns` 可恢复默认 300 轮。任务没有总执行时限，旧配置中的 `run.timeout` 现已忽略。 [单次调用时限](workspace-tools.md#输出与上限)

### 模型配置

`models.chat` 和 `models.image` 分别配置对话模型和生图模型。每组用 `default` 指定默认模型，用 `available` 列出候选模型。`--models-config` 可以单独指定模型目录。 [模型目录示例](../models.example.json)

单次任务按命令行参数、任务 JSON、环境变量、已保存的会话模型、默认模型的顺序选择模型。

| 配置位置 | 对话模型 | 生图模型 |
| --- | --- | --- |
| 命令行 | `--model` | `--image-model` |
| 任务 JSON | `model` | `image_model` |
| 环境变量 | `OPENROUTER_MODEL` | `OPENROUTER_IMAGE_MODEL` |

对话默认使用 Chat Completions。要启用 Responses，可传入 `--model-api responses`、在任务或用户配置中设置 `model_api`，或设置 `OPENROUTER_MODEL_API`。用模型目录的 `chat_apis` 为指定模型设置协议。 [OpenRouter Responses](https://openrouter.ai/docs/api_reference/responses/overview)

选择对话模型时，请确认其上下文窗口和最大输出满足固定预算。 [上下文预算与压缩](architecture.md#上下文预算与压缩)

## 构建和发布

PR 的 [Actions](https://github.com/xubaodian/let-the-agent-cook/actions) 构建三端安装包，在对应的托管 runner 安装，验证入口及 Python、Node.js、Chromium。部署方可用仓库变量 `AGENT_COOK_MODELS_JSON` 以 [models.example.json](../models.example.json) 的格式向安装包提供公开候选模型。也可在本地打包时传 `--models models.json`。

维护者发布时使用与 `agent/pyproject.toml` 匹配的版本标签，例如 `v0.1.0`。发布流水线安装锁定的依赖，签署 Windows MSI，签署并公证 macOS PKG，并构建供 HTTP 服务调用的 Linux amd64 和 arm64 工作区镜像。首次发布需要把 GHCR 中的 `agent-cook-sandbox` 包设为公开，供服务端拉取。流水线验证两个架构都可匿名读取后上传安装包。下载页源码位于 [docs/download/index.html](download/index.html)，可以从 `agent/docs/` 单独部署。 [Windows 安装范围](https://learn.microsoft.com/en-us/windows/win32/msi/installation-context) · [Apple 公证](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution) · [多架构镜像](https://docs.docker.com/build/building/multi-platform/)

Windows 签名使用 `WINDOWS_PFX_BASE64` 和 `WINDOWS_PFX_PASSWORD`。macOS 使用 `MACOS_APP_IDENTITY`、`MACOS_INSTALLER_IDENTITY` 及两个身份对应的 `MACOS_APP_P12_BASE64`、`MACOS_INSTALLER_P12_BASE64` 和各自的 `*_P12_PASSWORD`。公证使用 `MACOS_NOTARY_KEY_BASE64`、`MACOS_NOTARY_KEY_ID` 和 `MACOS_NOTARY_ISSUER`。

## 首发验收

在干净的 Windows 11 x64、Apple Silicon Mac 和 Ubuntu 24.04 x64 上用对应安装包验证。

1. 从安装脚本、开始菜单、应用程序或应用菜单启动。验证预填两把 Key 后可直接进入 CLI，也可在向导跳过 Exa。用 `/key exa` 更新 Exa，重启或重复安装后确认模型和 OpenRouter Key 仍能加载。
2. 运行 `agent-cook doctor`，确认原生隔离正常。执行 `/example` 给出的任务，核对首次准备的运行时能在第二次任务复用。
3. 用 `/open` 打开交付目录，对照包含中文、空格和子目录的样例，核对层级、处理后的副本和原始文件。以下脚本也可从已安装的 CLI 走完整路径，会调用已配置的 OpenRouter 模型。

| 系统 | 命令 |
| --- | --- |
| Windows PowerShell | `python installers/smoke.py "$env:LOCALAPPDATA/Programs/Agent Cook/agent-cook.exe" --files` |
| macOS | `python3 installers/smoke.py "/Applications/Agent Cook.app/Contents/Resources/agent-cook/agent-cook" --files` |
| Ubuntu | `python3 installers/smoke.py /usr/bin/agent-cook --files` |

4. 从仓库的 Agent 目录运行下列回归。三端矩阵在真实系统隔离中执行 Node.js、Python 和 Chromium，校验宿主读写、网络和密钥限制，以及文件层级、覆盖提交、取消和结果目录。

```bash
uv run --project . --locked --all-extras python -m unittest discover -s tests -v
AGENT_COOK_TEST_NATIVE=1 uv run --project . --locked --all-extras python -m unittest discover -s tests -p test_native_sandbox.py -v
```

PowerShell 中先设置 `$env:AGENT_COOK_TEST_NATIVE = '1'`，再运行相同的 `uv run` 命令。
