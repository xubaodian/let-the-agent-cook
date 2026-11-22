# 可运行的 Plugin 示例

三个 Studio 示例使用固定的示例数据和活动规则。请求用 `plugins` 按名称启用 Studio，用 `mcp_tools` 选用示例数据服务。6 个内置 Skill 随 CLI 可用。 [Studio 配置](../docs/plugins.md)

| 任务 | Plugin 与 MCP | 交付与验收 |
|---|---|---|
| [快速开始](quickstart/run.json) | 工作区工具 | HTML 与浏览器截图。 |
| [店铺周度复盘](shop-review/run.json) | `project-reporting-studio` 通过 stdio 连接 `fixture-data/describe_dataset` 和 `fixture-data/query_rows` | Excel、Markdown 简报、印尼语 PPTX 和指标 JSON。核对分页、重复订单、缺失金额及类目基准。 |
| [活动报名站](event-registration/run.json) | `reporting-studio` 通过 Streamable HTTP 连接 `reference-library/search` | 页面、活动内容、模拟接口和截图。验证空表单、满员与成功状态，以及改版后的重新截图。 |
| [双插件复盘](combined-review/run.json) | 两个 Plugin，选择 `fixture-data/query_rows` 与 `reference-library/search` | PPTX 和可筛选站点。对齐两份指标 JSON、验证筛选结果、复用 checkpoint 前的文件，并一起提交。 |

站点的 [双插件周报案例](../../site/src/content/cases/weekly-review.md) 有执行回放、筛选页和 PPTX。可按 [站点启动说明](../../site/README.md) 打开交互页面。

## 运行

在仓库的 `agent/` 目录配置 OpenRouter 后，用 `./cook demo` 在本地原生沙箱运行样例。[安装与启动](../README.md#安装与启动)

```bash
./cook demo
./cook demo shop-review
./cook demo combined-review
```

`fixture-data` 由执行入口自动启动。双插件样例按需启动宿主 `127.0.0.1:8766` 上的 `reference-library`，并在退出时回收。

报名站使用服务端 Docker 后端验证模拟接口。在服务端先用 `uv run --project . --no-sync python src/agent_cook/data/plugins/reporting-studio/mcp/server.py` 启动示例 MCP，再启动下面的 API。把 `event-registration/run.json` 的 `task` 字段提交到 `POST /v1/runs`。Playwright 会在任务沙箱内启动报名模拟接口。 [HTTP 服务接口](../docs/server.md#api)

```bash
./cook docker
./cook serve --sandbox docker --request examples/event-registration/run.json
```

提交报告时，`project-reporting-studio` 根据 `out/report/validation.json` 核对交付文件和检查结果。提交站点时，`reporting-studio` 核对一张通过断言的截图和站点文件版本。页面、样式或数据更新后，重新预览即可生成新凭据。两个 Hook 均按提交路径生效。

## 本地回归

```bash
uv run --project . --extra mcp --extra examples python -m unittest discover -s tests -p 'test_example_plugins.py' -v
```

默认回归使用脚本化模型和浏览器回执，执行实际的 CLI、MCP 服务、HTTP 接口、工作区 worker 和两个提交 Hook。双插件用例固定触发一次上下文压缩。使用以下命令运行真实 Chromium 交互。

```bash
uv run --project . --extra examples python -m playwright install chromium
AGENT_COOK_TEST_BROWSER=1 uv run --project . --extra mcp --extra examples \
  python -m unittest discover -s tests -p 'test_example_plugins.py' -v
```

报名站可在交付目录执行 `python serve.py` 后打开 `http://127.0.0.1:8075/`。双插件站点可在交付的 `out/site/` 对应目录执行 `python -m http.server 8075 --bind 127.0.0.1` 后打开同一地址。

## PowerPoint 验收

三个主题分别为门店经营复盘（4 页）、博物馆展览提案（5 页）和 Agent Cook 上手指南（4 页）。验收通过 Responses 调用 DeepSeek V4.1 Flash 1017 或 Grok 4.7，再通过 Seedream 5.5 生成封面。脚本检查 PPT 能打开、标题顺序和页数符合要求、正文可编辑，以及提交的封面已嵌入第一页。

在仓库的 `agent/` 目录配置 OpenRouter 并准备本地原生工作区后运行以下命令。输出目录下会保存通过检查的 PPT 和运行记录。GitHub Actions 中同名验收工作流可读取 `OPENROUTER_PPT_TEST_KEY` 仓库密钥。

```bash
uv run --project . --locked --extra examples python examples/pptx_validation.py --output ./pptx-validation
```

普通 CI 在 Docker 中通过模拟的模型和图片响应验证真实工作区执行与交付。产物 `pptx-protocol-fixtures` 用于检查协议和工具回归。
