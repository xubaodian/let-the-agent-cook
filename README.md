# Let the Agent Cook

一个用 Python 写的 Agent Runtime。当前入口接收一条任务，通过 OpenRouter 返回答复，并写出可供回放的 JSONL 执行记录。

## 本地运行

准备 Python 3.11 或更新版本、`uv` 和 OpenRouter API Key。把模型设为你账户可访问的模型 ID。

```bash
export OPENROUTER_API_KEY='your-key'
export OPENROUTER_MODEL='provider/model'
uv run --project agent agent-cook '用一句话介绍这个项目。'
```

标准输出是模型答复，执行记录的位置打印到标准错误。默认路径是 `.agent-cook/runs/<run_id>.jsonl`。可以用 `--trace` 选择一个新的输出文件。CLI 的工具注册表默认为空，运行文本任务时也能产出执行事件。

用 `--sandbox docker` 或 `--sandbox e2b` 启用工作区工具。工具清单、分层和运行命令见 [工作区工具与沙箱](docs/workspace-tools.md)。

使用沙箱时，可以用 `--skills-dir` 配置本机 Skill。模型先接收名称、描述和工作区入口，可用 `ls` 和 `read_file` 按需浏览和读取。[按需读取 Skill](docs/workspace-tools.md#按需读取-skill)

用 `--request run.json` 指定本次任务的插件、Skill 挂载名单和 MCP 工具，也可以直接传 `--plugin`、`--skill` 和 `--mcp-tool`。插件可以声明子 Agent、工具 Hook 和 MCP 服务。[Plugin 和任务扩展](docs/plugins.md)

仓库提供 9 个 Skill 和 `reporting-studio`、`web-studio` 两个示例插件，可以运行经营复盘、活动报名站和双插件交付任务。[示例和运行命令](examples/README.md)

任务可用 `web.enabled` 开启 OpenRouter 搜索和正文抓取，用 `web.browser_origins` 为 Playwright 指定公网来源。[网页检索和浏览器补查](docs/workspace-tools.md#网页检索和浏览器补查)

## 执行与上下文

公共执行提示词指导 Agent 按用户目标完成脚本、文件和工具操作，按需读取 Skill，验证并交付结果。工作区 Run 以主 Agent 的 `submit_results` 回执交付文件。Runtime 会核对已提交文件的内容，文件变更后需要提交当前版本才可完成。[公共执行提示词](agent/src/agent_cook/context/execution_prompt.md)

上下文默认按 32,768 tokens 的窗口计算。用 `--context-window` 填入实际模型窗口，或在 Python 入口传入 `AgentSpec.context_policy`。预扣输出及误差余量后，估算输入达到预算的 80% 时，Runtime 会在完整工具轮次之间尝试压缩。首次请求和压缩后重估整份输入，其余轮次以上一次模型返回的 `usage.prompt_tokens` 加新增消息的估算量为准。

压缩把已完成的旧轮次写成六段 Markdown checkpoint，继续使用同一工作区。模型保留任务原文和最近的完整调用与回执。单个轮次超出输入预算时也可压缩。成功的 checkpoint 及压缩前后的输入估算记录在 `context.compacted` 事件中。[上下文默认配置](agent/src/agent_cook/context/policy.py)

## Agent 包的目录

`agent/pyproject.toml` 定义依赖和 CLI。下面的路径相对于 `agent/src/agent_cook/`。

| 路径 | 职责 |
|---|---|
| `cli.py` | 读取配置，装配 OpenRouter 客户端和本地记录输出。 |
| `core/` | `run.py` 管 Run 的预算、结束状态和任务资源。`loop.py` 推进模型和工具轮次。 |
| `context/` | 管理执行提示词、输入预算、上下文组装和 checkpoint 压缩。 |
| `model/` | `client.py` 定义模型接口。`openrouter.py` 负责 Chat Completions 的消息和工具调用格式。 |
| `tools/` | `spec.py` 和 `registry.py` 管工具契约、授权和分派。`workspace.py` 注册工作区工具，`images.py` 接入生图，`artifacts.py` 导出交付文件。 |
| `extensions/` | 加载插件清单、Skill 包、子 Agent 定义、工具 Hook 和 MCP 配置。 |
| `sandbox/` | `interface.py` 定义任务环境接口。`docker.py` 和 `e2b.py` 管各自的生命周期和传输。`worker/` 在沙箱内执行共用操作。 |
| `state/` | 保存单次 Run 的原始消息、工具调用关联和当前 checkpoint。 |
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
