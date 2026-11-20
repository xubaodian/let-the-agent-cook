# Let the Agent Cook

一个用 Python 写的 Agent Runtime，提供本地交互 CLI 和常驻 HTTP 服务。两个入口都使用 OpenRouter，按任务执行并记录可回放的事件。

## 本地运行

在仓库根目录运行 `./cook setup`。`uv` 准备 Python 3.12 和项目依赖，并创建首次使用的配置文件。在 `.env` 填写 `OPENROUTER_API_KEY` 和你选择的 `OPENROUTER_MODEL` 后即可启动。

```bash
./cook setup
./cook
./cook run '用一句话介绍这个项目。'
```

`./cook doctor` 检查依赖、模型配置和已选沙箱。也可以在仓库根目录用 `uv tool install --python 3.12 ./agent` 安装独立 CLI，然后从任意目录运行 `agent-cook`。独立安装使用当前进程的环境变量。通过 `-C` 指定工作目录和相对路径的基准。

| 命令 | 用法 |
|---|---|
| `agent-cook` | 启动交互终端。可用 `@文件路径` 引用输入，`/model` 选模型，`/sandbox` 选工作区，`/help` 查看命令。 |
| `agent-cook exec "任务"` | 单次执行。用 `--input` 可重复选择文件，用 `exec -` 从 stdin 读取任务。 |
| `agent-cook resume --last` | 继续当前工作目录最近的会话，也可以用记录中的会话 ID。 |
| `agent-cook serve` | 启动常驻 HTTP API。[服务端使用方式](docs/server.md) |

交互时按 `Ctrl+C` 取消当前 Run。成功的 Run 保存对话、输入快照和最新交付的文件，续接时将它们重新放入工作区。用 `/new` 另开会话。已存在的 `agent-cook "任务"` 也是单次入口。

`exec` 默认把最终答复放在 stdout。用 `--json` 时 stdout 输出 JSONL 事件。执行记录和产物路径打印到 stderr，默认保存在工作目录的 `.agent-cook/runs/`。

文本任务可直接运行。需要工作区工具时使用 `--sandbox docker` 或 `--sandbox e2b`，也可设置 `AGENT_COOK_SANDBOX`。本地镜像可用 `./cook setup --docker` 准备。用 `./cook demo` 运行生成 HTML 和截图的快速示例。[工作区工具与沙箱](docs/workspace-tools.md)

使用沙箱时，可以用 `--skills-dir` 配置本机 Skill。模型先接收名称、描述和工作区入口，可用 `ls` 和 `read_file` 按需浏览和读取。[按需读取 Skill](docs/workspace-tools.md#按需读取-skill)

用 `exec --request run.json` 指定本次任务的插件、Skill 挂载名单和 MCP 工具，也可以直接传 `--plugin`、`--skill` 和 `--mcp-tool`。插件可以声明子 Agent、工具 Hook 和 MCP 服务。[Plugin 和任务扩展](docs/plugins.md)

仓库提供 9 个 Skill 和 `reporting-studio`、`web-studio` 两个示例插件，可以运行经营复盘、活动报名站和双插件交付任务。[示例和运行命令](examples/README.md)

任务可用 `web.enabled` 开启 OpenRouter 搜索和正文抓取，用 `web.browser_origins` 为 Playwright 指定公网来源。[网页检索和浏览器补查](docs/workspace-tools.md#网页检索和浏览器补查)

## 模型配置

要管理模型列表，可以复制示例为 `models.json`，在 `chat.available` 和 `image.available` 填写你可用的模型 ID。两组的 `default` 可填各自列表中的一个 ID，也可保留为 `null`。通过 `--models-config` 加载，`--list-models` 可在没有 API Key 时查看。[models.example.json](models.example.json)

```bash
uv run --project agent agent-cook --models-config ./models.json --list-models
uv run --project agent agent-cook --models-config ./models.json '用一句话介绍这个项目。'
```

两类模型各自按以下优先级选择。

| 优先级 | 对话模型 | 生图模型 |
|---|---|---|
| 1 | `--model` | `--image-model` |
| 2 | 任务 JSON 的 `model` | 任务 JSON 的 `image_model` |
| 3 | `OPENROUTER_MODEL` | `OPENROUTER_IMAGE_MODEL` |
| 4 | 配置文件的 `chat.default` | 配置文件的 `image.default` |

也可直接通过命令行、任务 JSON 或环境变量指定 OpenRouter 模型 ID。运行任务需要选择对话模型。在带沙箱的运行中选定生图模型后，Agent 可调用 `generate_image`。两类模型复用同一个 API Key。[生图用法](docs/workspace-tools.md#生图)

## 执行与上下文

公共执行提示词指导 Agent 按用户目标完成脚本、文件和工具操作，按需读取 Skill，验证并交付结果。工作区 Run 以主 Agent 的 `submit_results` 回执交付文件。Runtime 会核对已提交文件的内容，文件变更后需要提交当前版本才可完成。[公共执行提示词](agent/src/agent_cook/context/execution_prompt.md)

上下文默认按 32,768 tokens 的窗口计算。用 `--context-window` 填入实际模型窗口，或在 Python 入口传入 `AgentSpec.context_policy`。预扣输出及误差余量后，估算输入达到预算的 80% 时，Runtime 会在完整工具轮次之间尝试压缩。首次请求和压缩后重估整份输入，其余轮次以上一次模型返回的 `usage.prompt_tokens` 加新增消息的估算量为准。

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
| `model/` | `client.py` 定义模型接口。`openrouter.py` 负责 Chat Completions 的消息和工具调用格式。 |
| `tools/` | `spec.py` 和 `registry.py` 管工具契约、授权和分派。`workspace.py` 注册工作区工具，`images.py` 接入生图，`artifacts.py` 导出交付文件。 |
| `extensions/` | 加载插件清单、Skill 包、子 Agent 定义、工具 Hook 和 MCP 配置。 |
| `sandbox/` | `interface.py` 定义任务环境接口。`docker.py` 和 `e2b.py` 管各自的生命周期和传输。`worker/` 在沙箱内执行共用操作。 |
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
uv run --project agent python -m unittest discover -s agent/tests -v
```

测试用真实工作区子进程验证文件、命令和产物交付。模型、Docker、E2B 和 Chromium 的客户端调用使用替身。
