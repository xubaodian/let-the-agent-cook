# Agent Cook 核心实现

## 任务入口与执行循环

交互 CLI、`exec`、`resume` 和 HTTP 服务共用 `run_task`。它根据任务配置选择模型接口、注册工具、挂载扩展，再把任务交给 `run_agent`。后者管理本次运行的事件和工作区生命周期。 [CLI](../src/agent_cook/cli.py) · [HTTP 服务](server.md) · [任务组装](../src/agent_cook/runner.py) · [运行入口](../src/agent_cook/core/run.py)

执行循环在每轮先准备上下文，再调用模型。模型返回工具调用时，运行时校验调用 ID 和参数、执行工具，并把结果写回会话。模型返回最终答复时，运行时检查交付要求。CLI 的主执行循环默认最多 300 轮，单次模型调用含重试最多 5 分钟。 [执行循环](../src/agent_cook/core/loop.py) · [执行提示词](../src/agent_cook/context/execution_prompt.md) · [重试行为](workspace-tools.md#瞬时故障重试)

## 模型接口

两种文本适配器通过 OpenAI SDK 访问 OpenRouter。Chat Completions 是默认接口，也可按模型选择 Responses。执行循环消费统一的 `ModelTurn`，其中包含消息、工具调用、结束原因和输入用量。生图通过单独配置的图像模型和工作区工具执行。 [模型配置](install.md#模型配置) · [Chat Completions 适配器](../src/agent_cook/model/openrouter.py) · [Responses 适配器](../src/agent_cook/model/openrouter_responses.py) · [生图工具](workspace-tools.md#生图)

## 上下文预算与压缩

| 项目 | 预算 | 行为 |
| --- | ---: | --- |
| 单次请求输入 | **360,000 tokens** | 发送前估算输入。超过时尝试压缩历史，恰好等于阈值时可发送。 |
| 单次文本模型输出 | **50,000 tokens** | 每次请求都下发上限。Chat Completions 用 `max_completion_tokens`，Responses 用 `max_output_tokens`。主 Agent、子 Agent 和 checkpoint 请求共用该上限。 |
| Checkpoint 正文 | **约 1,600 tokens** | 压缩提示要求简洁，收到结果后按本地估算校验正文长度和标题。 |
| CLI 任务完成后的会话历史 | **32,000 tokens** | 交互式任务成功后估算有效历史，超过时生成供下一条任务使用的 checkpoint。该估算只计会话内容。 |

这些值由运行时固定。请选择支持 **至少 50,000 tokens 输出**的模型和服务商路由，并为输入和输出同时达到上限留出 **410,000 tokens 加估算余量**的上下文。旧版 `run.context_window` 和 `--context-window` 仍可读取或解析，对当前预算无影响。 [预算定义](../src/agent_cook/context/policy.py) · [OpenRouter Chat Completions](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion) · [OpenRouter Responses](https://openrouter.ai/docs/api/api-reference/responses/create-a-response)

1. **估算下一轮输入。** 系统提示、任务、历史消息、工具定义、参数和结果都计入预算。首次按 UTF-8 字节数粗估。模型返回输入用量后，若下一轮保留原请求前缀，则以该用量为基准加上新增消息估算。前缀变化或用量包含服务端网页工具上下文时，使用本地估算。 [预算估算](../src/agent_cook/context/budget.py)
2. **确定要总结的历史。** 执行中超过 360K 时，优先保留最近两轮完整模型交互，包含工具调用和结果。保留内容过大时缩短这一段。当前任务的用户请求保留原文。 [上下文组装](../src/agent_cook/context/builder.py) · [压缩实现](../src/agent_cook/context/compaction.py)
3. **调用模型生成 checkpoint。** 输入包含当前任务、上次 checkpoint、待替换历史和可用的工作区信息。已打开工作区且允许 `ls` 时，运行时读取最多 80 个相对文件路径，并附上本次已提交的路径。清单省略文件时标记截断。压缩请求同样受 360K 输入预算约束，必要时缩短历史中的长工具参数和结果。 [工作区清单](../src/agent_cook/core/loop.py) · [Checkpoint 提示词](../src/agent_cook/context/prompts.py)
4. **校验后继续。** 校验成功后，后续请求用 checkpoint 和保留的近期消息重建历史。标题或长度不合格、压缩后仍超预算时，运行时用更短的压缩请求重试一次。仍无法缩小输入，或原始任务已超预算且没有可压缩历史时，任务给出错误。开始、重试、成功和失败记录在运行事件中。 [压缩实现](../src/agent_cook/context/compaction.py) · [事件日志](../src/agent_cook/events/journal.py)

交互 CLI 在最终答复和交付检查完成后，按 32K 历史阈值判断是否生成会话 checkpoint。压缩成功后，下一条任务与 `resume --last` 使用 checkpoint、最近的完整用户请求（共约 4,096 tokens 以内）及其后的新消息。原先的助手答复、推理和工具调用与结果由摘要接替。Checkpoint 保留任务结论、用户偏好、交付路径、验证状态和待办。压缩失败或取消时，CLI 照常交付，并沿用之前的上下文。单次 `exec` 和 HTTP 服务沿用执行中的 360K 压缩。 [完成检查与压缩](../src/agent_cook/core/loop.py) · [CLI 会话](../src/agent_cook/cli.py)

Checkpoint 使用固定的六个标题，供下一轮恢复任务状态。

| 标题 | 保留内容 |
| --- | --- |
| 任务与当前状态 | 目标、约束和当前阶段。 |
| 已完成工作 | 有工具结果或记录支持的已完成事项。 |
| 当前产物或者文件 | 工作区路径、文件作用和已提交状态。 |
| 工具与验证状态 | 已通过的检查、检查覆盖的文件版本、修改后待复验的事项。 |
| 任务进度 | 已读取的 Skill 入口或章节和接下来应做的操作。 |
| 未完成与不确定事项 | 待办、失败尝试和需要补证的结论。 |

## 工具、沙箱与扩展

`ToolRegistry` 负责向模型暴露工具定义，按任务允许的工具名单分发调用，校验 JSON 参数并执行 Hook。工具通过 `Sandbox` 接口读写文件、执行脚本或运行浏览器。首次用到工作区时，`SandboxFactory` 才打开后端并导入输入副本。CLI 默认使用本机隔离，服务端可接 Docker 或 E2B。运行结束或取消时释放工作区。 [工具注册表](../src/agent_cook/tools/registry.py) · [沙箱接口](../src/agent_cook/sandbox/interface.py) · [工作区与隔离](workspace-tools.md)

Skill 默认注入名称、描述和入口，由模型按需读取正文。Plugin 通过清单引用 Skill，并可提供工作流指令、素材、子 Agent、Hook 及 MCP 配置。MCP 服务中的每个选中工具独立注册到同一个工具表。`delegate` 让子 Agent 以独立会话运行同一个执行循环，并在父任务的工作区内使用获准的工具子集。 [Skill 加载](../src/agent_cook/extensions/skills.py) · [Plugin 协议](plugins.md) · [MCP 适配](../src/agent_cook/extensions/mcp.py) · [子 Agent](../src/agent_cook/core/delegation.py)

## 状态、回放与交付

CLI 用 SQLite 保存会话历史、checkpoint、输入副本及交付快照。压缩改变后续请求携带的上下文，原消息仍可随会话保存。运行期间的事件记录模型和工具进度、checkpoint、产物引用及 UI 附件。CLI 写入 JSONL，HTTP 服务将事件持久化后通过 SSE 提供查阅和续接。 [会话存储](../src/agent_cook/state/local.py) · [事件协议](../src/agent_cook/protocol/events.py) · [UI 附件](../src/agent_cook/protocol/ui.py) · [HTTP 事件接口](server.md#api)

`submit_results` 导出被选中的文件。同一路径以最近一次成功提交为准，结束前校验工作区内的文件内容是否与提交版本一致。CLI 在任务成功后把最终文件按相对路径归集到 `<run_id>-results/`，并可通过 `/open` 打开。 [提交与校验](../src/agent_cook/tools/artifacts.py) · [交付目录](../src/agent_cook/delivery.py)

本地 CLI 将启动目录用于解析用户输入，公共工作区用于持久化会话与交付。会话存储按启动目录区分，结果目录按会话创建日期和 ID 组织。全局源码安装使用独立版本目录，入口在新版本通过启动检查后切换。运行中的 CLI 会持有对应版本的共享锁。 [输入路径与结果归档](install.md#输入路径与结果归档) · [更新实现](../src/agent_cook/updates.py) · [版本入口](../src/agent_cook/managed_launch.py)
