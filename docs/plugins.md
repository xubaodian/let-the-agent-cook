# Plugin 和任务扩展

任务请求决定本次运行启用哪些 Plugin、挂载哪些 Skill，以及向模型开放哪些 MCP 工具。`RunRequest.parse()` 也可以用于接受 JSON 对象的宿主入口。

```json
{
  "schema_version": 1,
  "task": "用项目数据生成简报，复核关键数字后提交",
  "model": "provider/chat-model",
  "plugins": ["./plugins/reporting"],
  "skills": ["brief"],
  "mcp_tools": ["project-data/lookup"]
}
```

文件里的相对目录从请求文件的目录解析。用 `--request run.json` 读取任务，通过 `--sandbox` 和 `--input` 配置沙箱与输入文件。任务可用 `model` 选择对话模型，用 `image_model` 选择生图模型。命令行参数可以覆盖两项选择。[模型配置](install.md#模型配置)

```bash
uv run --project agent agent-cook --sandbox docker --request run.json
```

| 字段 | 填写内容 | 作用 |
|---|---|---|
| `skills` | Skill 名称，例如 `brief` | 用同一份名单构建模型目录并挂载到工作区 |
| `mcp_tools` | `server/tool`，例如 `project-data/lookup` | 只连接名单涉及的服务器，向模型注册所列工具 |

两个数组省略时使用对应的全部发现项，`[]` 表示选零个。`mcp_tools` 为 `[]` 时跳过 MCP 服务连接。填写未知的 Skill 或 MCP 工具名称会报错。独立的 Skill 包目录可通过 `skill_directories` 指定，例如 `["./my-skills"]`。

命令行也可以用 `--plugin ./plugins/reporting`、`--skill brief` 和 `--mcp-tool project-data/lookup`，这些参数都可重复。工作区第一次打开时复制选中的包和插件文件，模型起始上下文只包含 Skill 的名称、描述和入口。`extensions.loaded` 记录启用的插件、Skill 和注册到模型的 MCP 工具名，例如 `mcp__project-data__lookup`。

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

`agents/data-reviewer.md` 的 frontmatter 声明角色，正文是子 Agent 的指令。`model` 省略或为 `inherit` 时使用父 Agent 模型。`max_model_turns` 受父任务轮次限制。可用 `timeout_seconds` 显式限制一次 `delegate` 调用。

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

可选的 `mcp.json` 使用 Agent Plugins 的 MCP schema 和 `mcpServers` 对象。支持 `stdio` 与 `streamable-http`。MCP 客户端随 Agent Cook 安装。[Agent Plugins MCP 运行规范](https://agent-plugins.org/client-implementers/mcp-runtime)

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

把示例 URL 换成实际服务。任务请求中的 `project-data/lookup` 使用 `mcpServers` 下的服务名 `project-data`，以及服务端公布的工具名 `lookup`。`stdio` 由宿主启动选中的本地服务，支持 `command`、`args`、`env` 和插件内的 `cwd`。运行时在首轮模型调用前发现工具，把文本或结构化结果交回 Agent，并在任务退出时关闭连接。

宿主直接连接 `localhost`、`127.0.0.1` 和 `::1` 上的 HTTP MCP 服务。[本地 MCP 示例](../examples/README.md)
