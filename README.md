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

## Agent 包的目录

`agent/pyproject.toml` 定义依赖和 CLI。下面的路径相对于 `agent/src/agent_cook/`。

| 路径 | 职责 |
|---|---|
| `cli.py` | 读取配置，装配 OpenRouter 客户端和本地记录输出。 |
| `core/` | `run.py` 管 Run 的预算、结束状态和任务资源。`loop.py` 推进模型和工具轮次。 |
| `context/` | `builder.py` 从指令和会话历史组装本轮模型输入。 |
| `model/` | `client.py` 定义模型接口。`openrouter.py` 负责 Chat Completions 的消息和工具调用格式。 |
| `tools/` | `spec.py` 和 `registry.py` 管工具契约、授权和分派。`workspace.py` 注册工作区工具，`artifacts.py` 导出交付文件。 |
| `sandbox/` | `interface.py` 定义任务环境接口。`docker.py` 和 `e2b.py` 管各自的生命周期和传输。`worker/` 在沙箱内执行共用操作。 |
| `state/` | 保存单次 Run 中原始消息及其工具调用关联。 |
| `protocol/` | 定义可序列化的 RunEvent、ArtifactRef 和 UiAttachment，供未来的前端和回放读取。 |
| `events/` | 为执行事件编号并写入 JSONL。 |

一个 Run 在首次需要时获取沙箱。之后的工具共用同一任务环境，Run 在完成、失败或取消时退出该环境。工具记录与会话历史分别服务于回放和模型输入。

## 扩展接入点

- **上下文压缩**放进 `context/`，在完整工具轮次结束后生成检查点。会话历史由 `state/` 管理。
- **Skill、Plugin 和 MCP**集中到 `extensions/`。Skill 按需加载，Plugin 装配声明，MCP 把外部工具注册到同一个工具注册表。
- **新的沙箱后端**实现 `sandbox/interface.py`，通过共用的工作区执行模块提供现有工具行为。仓库根目录的 `sandbox/` 放运行镜像。
- **子 Agent**由 `core/` 调用同一个运行入口。父 Run 分配工作区、工具和预算，并收回子任务的产物。
- **作品站**从 `examples/` 中的公开轨迹和产物读取数据。`site/` 负责界面渲染，包括实时宿主适配和静态回放。

## 本地验证

```bash
uv run --project agent python -m unittest discover -s agent/tests -v
```

测试用真实工作区子进程验证文件、命令和产物交付。模型、Docker、E2B 和 Chromium 的客户端调用使用替身。
