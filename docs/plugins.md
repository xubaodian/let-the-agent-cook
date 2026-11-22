# Plugin 和任务扩展

启用工作区时，Agent Cook 会挂载 6 个内置 Skill。首轮只提供名称、描述和入口，Agent 按任务读取相应的 `SKILL.md`。如果输入占用了同名路径，Agent Cook 会保留输入原路径，并为内置 Skill 选择一个空闲目录。

| Skill | 用途 |
| --- | --- |
| `pptx` | 制作和检查可编辑的 PowerPoint。 |
| `static-site` | 构建网站、预览交互并保存截图。 |
| `data-analysis` | 清洗和核对数据、计算指标及生成 Excel。 |
| `brief` | 整理简报、项目进展与交付材料。 |
| `research-synthesis` | 检索资料、追溯来源并汇总证据。 |
| `image-assets` | 规划、生成并检查网站及 PPT 的配图。 |

## 启用 Studio

用 `--plugin` 显式启用 Studio。普通启动载入上述 Skill，Plugin 由本次命令或任务请求选择。

```bash
./cook --list-plugins
./cook --plugin reporting-studio
./cook --plugin project-reporting-studio
```

Windows 源码使用 `.\cook.cmd`，已在终端安装 CLI 时使用 `agent-cook`。也可在 `exec` 后传入 `--plugin`，为单次任务启用。

| Studio | 工作流程 | 引用的 Skill |
| --- | --- | --- |
| `reporting-studio` | 通用报告、PPT 和网站交付，提供网站模板与浏览器复核。 | PPT、网站、数据、汇报、调研和配图。 |
| `project-reporting-studio` | 项目进度、风险、决策和下一步，提供数据复核与提交检查。 | `pptx`、`data-analysis`、`brief`。 |

两套 Studio 共用 Skill 文件。示例 MCP 通过 `--mcp-tool` 或任务的 `mcp_tools` 显式选用。 [带数据和浏览器验证的示例](../examples/README.md)

## 任务请求

```json
{
  "schema_version": 1,
  "task": "梳理项目进展，整理风险、待决策事项和下一步，生成可编辑 PPT",
  "plugins": ["project-reporting-studio"]
}
```

用 `./cook exec --request run.json` 执行。可以传入 `--input` 添加资料。任务可用 `model` 选择对话模型，用 `image_model` 选择生图模型。也可用 `RunRequest.parse()` 从代码接收请求。 [模型配置](install.md#模型配置)

| 字段 | 用途 |
| --- | --- |
| `plugins` | 内置名称或插件目录。相对目录以请求文件的位置为基准。 |
| `skill_directories` | 加入自定义 Skill 目录，同名自定义 Skill 优先于内置版。 |
| `skills` | 省略时挂载全部发现的 Skill。给出数组时，挂载所列 Skill 和启用的 Plugin 引用的 Skill。 |
| `mcp_tools` | 选择 `server/tool`。空数组跳过插件 MCP 连接。 |

命令行中可以重复使用 `--plugin`、`--skills-dir`、`--skill` 和 `--mcp-tool`。工作区首次打开时复制已选的包和插件文件。`extensions.loaded` 记录启用的 Plugin、Skill 和注册的 MCP 工具。

## 插件目录

插件根目录中的 `$schema` 使用 Agent Plugins 1.0.0 的公开清单标识。Skill 和 MCP 配置采用规范中的固定目录，`agents/` 和 `hooks/` 由本项目的运行时加载。[Agent Plugins 加载规范](https://agent-plugins.org/client-implementers/loading-and-discovery)

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "team-reporting",
  "version": "0.1.0",
  "extensions": {
    "com.github.xubaodian.agent-cook": {
      "skills": ["brief", "pptx"],
      "instructions": "核对数据及来源后再提交团队汇报。"
    }
  }
}
```

| 插件路径 | 用途 |
|---|---|
| `plugin.json` | 根清单。Agent Cook 扩展可按名称引用已发现的 Skill，并提供工作流程指令。 |
| `skills/custom/SKILL.md` | 可选的插件专属 Skill。选中后可从工作区的 `custom/SKILL.md` 读取。 |
| `assets/` | 复制插件资产，按 `assets/...` 工作区路径访问 |
| `agents/*.md` | 配置插件可提供的子 Agent |
| `hooks/hooks.json` | 定义工具 Hook 和包内脚本的路径 |
| `mcp.json` | 声明插件要连接的 MCP 服务 |

MCP 服务随插件启用时自动连接。将 Agent Cook 扩展中的 `connect_mcp_by_default` 设为 `false`，即可改用 `mcp_tools` 或 `--mcp-tool` 选择该插件的服务工具。

## 子 Agent

`agents/data-reviewer.md` 的 frontmatter 声明角色，正文是子 Agent 的指令。`model` 省略或为 `inherit` 时使用父 Agent 模型。父任务或角色可显式设置轮次上限，两者都设置时取较小值。角色也可用 `timeout_seconds` 显式限制一次 `delegate` 调用。

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
