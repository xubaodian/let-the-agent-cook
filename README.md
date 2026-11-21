# Let the Agent Cook

一个用 Python 写的 Agent Runtime，提供本地交互 CLI 和常驻 HTTP 服务。两个入口都使用 OpenRouter，按任务执行并记录可回放的事件。

## 安装使用

从 [GitHub Releases](https://github.com/xubaodian/let-the-agent-cook/releases/latest) 下载 Windows、Apple Silicon macOS 或 Ubuntu 安装包。打开 Agent Cook 后连接 OpenRouter，可通过 `/example` 体验文件夹任务，通过 `/open` 查看结果。[安装和验收步骤](docs/install.md)

## 从源码运行

解压源码后，macOS 和 Linux 在源码目录运行 `./install`，Windows 双击 `install.cmd`。安装脚本准备 uv、Python 和锁定的依赖，随后打开 CLI。下次用 `./cook` 或 `cook.cmd` 启动，重复安装可修复依赖。[安装步骤](docs/install.md)

```bash
./install
./cook
./cook run '用一句话介绍这个项目。'
```

`./cook doctor` 检查依赖、模型配置和已选沙箱。也可以在仓库根目录用 `uv tool install --python 3.12 './agent[examples]'` 安装独立 CLI，然后从任意目录运行 `agent-cook`。通过 `-C` 指定工作目录和相对路径的基准。

| 命令 | 用法 |
|---|---|
| `agent-cook` | 启动交互终端。可用 `@路径` 选择文件或文件夹，`/model` 选模型，`/sandbox` 选工作区，`/help` 查看命令。 |
| `agent-cook exec "任务"` | 单次执行。用 `--input` 可重复选择文件或文件夹，用 `exec -` 从 stdin 读取任务。 |
| `agent-cook resume --last` | 继续当前工作目录最近的会话，也可以用记录中的会话 ID。 |
| `agent-cook docker` | 为服务端 Docker 后端准备任务镜像。 |
| `agent-cook serve` | 启动常驻 HTTP API。[服务端使用方式](docs/server.md) |

交互时按 `Ctrl+C` 取消当前 Run。成功的 Run 保存对话、输入快照和最新交付的文件，续接时将它们重新放入工作区。用 `/new` 另开会话。已存在的 `agent-cook "任务"` 也是单次入口。

`exec` 默认把最终答复放在 stdout。用 `--json` 时 stdout 输出 JSONL 事件。执行记录和结果目录的绝对路径打印到 stderr，默认保存在工作目录的 `.agent-cook/runs/`。

本地 CLI 默认在系统原生沙箱里执行脚本和浏览器。文件任务在独立工作区中处理副本，纯对话按需打开工作区。`--sandbox none` 可关闭本地工作区。用 `./cook demo` 运行生成 HTML 和截图的快速示例。[工作区工具与沙箱](docs/workspace-tools.md)

使用沙箱时，可以用 `--skills-dir` 配置本机 Skill。模型先接收名称、描述和工作区入口，可用 `ls` 和 `read_file` 按需浏览和读取。[按需读取 Skill](docs/workspace-tools.md#按需读取-skill)

用 `exec --request run.json` 指定本次任务的插件、Skill 挂载名单和 MCP 工具，也可以直接传 `--plugin`、`--skill` 和 `--mcp-tool`。插件可以声明子 Agent、工具 Hook 和 MCP 服务。[Plugin 和任务扩展](docs/plugins.md)

仓库提供 9 个 Skill 和 `reporting-studio`、`web-studio` 两个示例插件，可以运行经营复盘、活动报名站和双插件交付任务。[示例和运行命令](examples/README.md)

配置 `api_keys.exa` 或 `EXA_API_KEY` 后自动获得 Exa 网页搜索和正文抓取。服务端任务可用 `web.browser_origins` 为 Playwright 指定公网来源。[网页检索和浏览器补查](docs/workspace-tools.md#网页检索和浏览器补查)

## 启动和模型配置

源码、安装包和 Server 默认共用系统用户目录中的 `agent-cook.json`。打开 CLI 后，向导可连接 OpenRouter、填写可选的 Exa Key 并选择模型。用 `/config` 查看配置的绝对路径，用 `/key` 换 Key，用 `/model <ID>` 更换默认模型。也可以直接编辑文件，重启后生效。[配置路径和命令](docs/install.md#配置) · [agent-cook.example.json](agent-cook.example.json)

已有项目配置可用 `--config` 指定。`--models-config` 仍可单独指定模型目录。环境变量可覆盖配置里的对应值，源码启动器也会读取已有的 `.env`。旧系统凭据库和模型设置可作为兼容来源。

```bash
./cook --list-models
./cook run '用一句话介绍这个项目。'
```

两类模型各自按以下优先级选择。

| 优先级 | 对话模型 | 生图模型 |
|---|---|---|
| 1 | `--model` | `--image-model` |
| 2 | 任务 JSON 的 `model` | 任务 JSON 的 `image_model` |
| 3 | `OPENROUTER_MODEL` | `OPENROUTER_IMAGE_MODEL` |
| 4 | 续接会话的模型 | 续接会话的生图模型 |
| 5 | `agent-cook.json` 中的 `models.chat.default` | 模型目录的 `image.default` |
| 6 | 旧版 `settings.json` 或兼容模型目录的 `chat.default` | |

也可直接通过命令行、任务 JSON 或环境变量指定 OpenRouter 模型 ID。运行任务需要选择对话模型。在带沙箱的运行中选定生图模型后，Agent 可调用 `generate_image`。两类模型复用同一个 API Key。[生图用法](docs/workspace-tools.md#生图)

对话模型可使用 Chat Completions 或 Responses。按顺序读取 `--model-api`、任务 JSON 的 `model_api`、`OPENROUTER_MODEL_API`、`agent-cook.json` 的 `model_api`，然后查模型目录的 `chat_apis`。模型目录可为不同模型指定协议，例如 `"chat_apis": {"deepseek/deepseek-v4.1-flash-1017": "responses"}`。默认使用 `chat_completions`。

Responses 请求由本地 Session 提供上下文，工具结果按 `call_id` 回传。OpenRouter 的 Responses 接口采用无状态请求。 [OpenRouter 接口文档](https://openrouter.ai/docs/api_reference/responses/overview)

## 执行与上下文

公共执行提示词指导 Agent 按用户目标完成脚本、文件和工具操作，按需读取 Skill，验证并交付结果。工作区 Run 以主 Agent 的 `submit_results` 回执交付文件。Runtime 会核对已提交文件的内容，文件变更后需要提交当前版本才可完成。[公共执行提示词](agent/src/agent_cook/context/execution_prompt.md)

上下文默认按 32,768 tokens 的窗口计算。用 `--context-window` 填入实际模型窗口，或在 Python 入口传入 `AgentSpec.context_policy`。预扣输出及误差余量后，估算输入达到预算的 80% 时，Runtime 会在完整工具轮次之间尝试压缩。首次请求和压缩后重估整份输入，其余轮次以上一次模型返回的输入 token 数加新增消息的估算量为准。

压缩把已完成的旧轮次写成六段 Markdown checkpoint，继续使用同一工作区。模型保留任务原文和最近的完整调用与回执。单个轮次超出输入预算时也可压缩。成功的 checkpoint 及压缩前后的输入估算记录在 `context.compacted` 事件中。[上下文默认配置](agent/src/agent_cook/context/policy.py)

## Agent 包的目录

`agent/pyproject.toml` 定义依赖和 CLI。下面的路径相对于 `agent/src/agent_cook/`。

| 路径 | 职责 |
|---|---|
| `cli.py`、`terminal.py` | 本地交互、命令行配置和输出。 |
| `runner.py` | 为 CLI 和 Server 组装模型、工具与沙箱，执行单次 Run。 |
| `server/` | HTTP API、持久化任务队列和 SSE。 |
| `core/` | `run.py` 管 Run 的预算、结束状态和任务资源。`loop.py` 推进模型和工具轮次。 |
| `context/` | 管理执行提示词、输入预算、上下文组装和 checkpoint 压缩。 |
| `model/` | `client.py` 定义模型接口。`openrouter.py` 和 `openrouter_responses.py` 适配 OpenRouter 的两种对话协议。 |
| `tools/` | `spec.py` 和 `registry.py` 管工具契约、授权和分派。`workspace.py` 注册工作区工具，`images.py` 接入生图，`artifacts.py` 导出交付文件。 |
| `extensions/` | 加载插件清单、Skill 包、子 Agent 定义、工具 Hook 和 MCP 配置。 |
| `sandbox/` | `interface.py` 定义任务环境接口。`native/` 提供本地系统隔离。`docker.py` 和 `e2b.py` 管服务端后端的生命周期和传输。`worker/` 提供共用工作区操作。 |
| `state/` | 管理消息、checkpoint 和本地交互会话的输入与产物快照。 |
| `protocol/` | 定义任务入口、RunEvent、ArtifactRef 和 UiAttachment，供宿主接入和回放读取。 |
| `events/` | 为执行事件编号并写入 JSONL。 |

一个 Run 在首次需要时获取沙箱。之后的工具共用同一任务环境，Run 在完成、失败或取消时退出该环境。工具记录与会话历史分别服务于回放和模型输入。

## 扩展接入点

- **Skill、Plugin 和 MCP**集中到 `extensions/`。任务请求选定 Skill，Plugin 装配声明，MCP 把外部工具注册到同一个工具注册表。
- **新的沙箱后端**实现 `sandbox/interface.py`，通过共用的工作区执行模块提供现有工具行为。仓库根目录的 `sandbox/` 放运行镜像。
- **子 Agent**由 `core/` 复用模型循环同步执行。父 Run 分配工作区、工具和预算，并收回子任务的产物。
- **作品站**从 `examples/` 中的公开轨迹和产物读取数据。`site/` 负责界面渲染，包括实时宿主适配和静态回放。

## 本地验证

```bash
uv run --project agent --locked --all-extras python -m unittest discover -s agent/tests -v
```

CI 在 Windows x64、macOS arm64 和 Ubuntu x64 实测本地脚本、浏览器、隔离和交付，并为 Docker 服务端后端单独运行回归。[首发验收](docs/install.md#首发验收)
