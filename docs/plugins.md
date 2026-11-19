# Plugin 和任务扩展

任务请求决定本次运行启用哪些 Plugin，以及哪些 Skill 进入模型目录和沙箱工作区。`RunRequest.parse()` 也可以用于接受 JSON 对象的宿主入口。

```json
{
  "schema_version": 1,
  "task": "生成项目简报，复核关键数字后提交",
  "plugins": ["./plugins/reporting"],
  "skills": {
    "directories": ["./my-skills"],
    "mount": ["brief"]
  }
}
```

文件里的相对目录从请求文件的目录解析。用 `--request run.json` 读取任务，模型、沙箱和输入文件仍通过 `--model`、`--sandbox` 和 `--input` 提供。

```bash
uv run --project agent agent-cook --sandbox docker --request run.json
```

| `skills.mount` | 本次运行的 Skill |
|---|---|
| 省略 | 启用从 `skills.directories` 和已启用 Plugin 发现的全部 Skill |
| 空数组 `[]` | 本次任务挂载零个 Skill |
| 名称数组 | 系统提示和工作区都只包含所列 Skill，未知名称会报错 |

命令行也可以直接用 `--plugin ./plugins/reporting` 启用目录，用 `--skill brief` 选中 Skill。可重复传这些参数。工作区第一次打开时复制选中的包和插件文件，模型起始上下文只包含 Skill 的名称、描述和入口。运行日志的 `extensions.loaded` 记录本次启用的插件和 Skill 名单。

## 插件目录

插件根目录中的 `$schema` 使用 Agent Plugins 1.0.0 的公开清单标识。Skill 和 MCP 配置采用规范中的固定目录，`agents/` 和 `hooks/` 由本项目的运行时加载。[Agent Plugins 加载规范](https://agent-plugins.org/client-implementers/loading-and-discovery)

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "reporting",
  "version": "0.1.0"
}
```

| 插件路径 | 用途 |
|---|---|
| `plugin.json` | 根清单，提供插件身份 |
| `skills/brief/SKILL.md` | 发现 `brief`，选中后在工作区通过 `brief/SKILL.md` 访问 |
| `assets/` | 复制插件资产，按 `assets/...` 工作区路径访问 |
| `agents/*.md` | 配置插件可提供的子 Agent |
| `hooks/hooks.json` | 定义工具 Hook 和包内脚本的路径 |
| `mcp.json` | 声明插件要连接的 MCP 服务 |

## 子 Agent

`agents/data-reviewer.md` 的 frontmatter 声明角色，正文是子 Agent 的指令。`model` 省略或为 `inherit` 时使用父 Agent 模型。可选的 `max_model_turns` 和 `timeout_seconds` 会受到父任务预算约束。

```markdown
---
name: data-reviewer
description: 核对简报里的数字和原始数据。
tools: [ls, read_file]
---

读取委派任务指定的简报和数据，返回差异、来源和修正建议。
```

启用含子 Agent 的插件后，主 Agent 可以通过 `delegate` 指定 `agent` 和 `task`。一次调用在独立会话中同步执行，使用父任务的真实工作区。有效工具取角色配置和父任务授权的交集。JSONL 记录子任务的父 Run ID 与结果，父 Agent 通过工具响应接收结果。

## 工具 Hook

`hooks/hooks.json` 可以为指定工具配置 `before_tool` 和 `after_tool`。下面的配置在 `submit_results` 执行前运行 `hooks/check.py`。

```json
{
  "hooks": [
    {
      "event": "before_tool",
      "tool": "submit_results",
      "script": "hooks/check.py",
      "timeout_seconds": 10
    }
  ]
}
```

Python 脚本在任务沙箱里执行，工作目录是工作区根目录。从 stdin 读取 JSON，字段包括 `event`、`tool`、`arguments`、`tool_call_id`、`run_id`，后置 Hook 还会收到 `result`。运行时在加载插件时读取脚本源码，实际执行使用这份源码。

前置 Hook 的非零退出会拦截当前工具调用，校验原因会交回 Agent。后置 Hook 的执行结果写入 `hook.finished`，工具结果依照原执行结果返回。已有沙箱镜像需重新构建以运行 Hook。[工作区工具与沙箱](workspace-tools.md)

## MCP

可选的 `mcp.json` 使用 Agent Plugins 的 MCP schema 和 `mcpServers` 对象。支持 `stdio` 与 `streamable-http`。安装 MCP 依赖时可传 `uv sync --project agent --extra mcp`。[Agent Plugins MCP 运行规范](https://agent-plugins.org/client-implementers/mcp-runtime)

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
  "mcpServers": {
    "project-data": {
      "type": "streamable-http",
      "url": "https://example.invalid/mcp"
    }
  }
}
```

把示例 URL 换成实际服务。`stdio` 由宿主启动显式启用的本地服务，支持 `command`、`args`、`env` 和插件内的 `cwd`。运行时在首轮模型调用前发现工具，把文本或结构化结果交回 Agent，并在任务退出时关闭连接。
