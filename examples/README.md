# 可运行的 Plugin 示例

三个任务使用固定的示例数据和活动规则。通用 Skill 在仓库的 `skills/`，其余由两个 Plugin 提供。请求文件里的 `skills` 选择本次挂载的名称，`mcp_tools` 选择本次向模型开放的工具。

| 任务 | Plugin 与 MCP | 交付与验收 |
|---|---|---|
| [快速开始](quickstart/run.json) | 工作区工具 | HTML 与浏览器截图。 |
| [店铺周度复盘](shop-review/run.json) | `reporting-studio` 通过 stdio 连接 `fixture-data/describe_dataset` 和 `fixture-data/query_rows` | Excel、Markdown 简报、印尼语 PPTX 和指标 JSON。核对分页、重复订单、缺失金额及类目基准。 |
| [活动报名站](event-registration/run.json) | `web-studio` 通过 Streamable HTTP 连接 `reference-library/search` | 页面、活动内容、模拟接口和截图。验证空表单、满员与成功状态，以及改版后的重新截图。 |
| [双插件复盘](combined-review/run.json) | 两个 Plugin，选择 `fixture-data/query_rows` 与 `reference-library/search` | PPTX 和可筛选站点。对齐两份指标 JSON、验证筛选结果、复用 checkpoint 前的文件，并一起提交。 |

## 运行

在仓库根目录配置 OpenRouter 后，用 `./cook demo` 构建工作区镜像并运行样例。[本地运行](../README.md#本地运行)

```bash
./cook demo
./cook demo shop-review
./cook demo event-registration
./cook demo combined-review
```

`fixture-data` 由执行入口自动启动。报名站和双插件的样例用宿主 `127.0.0.1:8766` 上的 `reference-library`，按需启动进程并在退出时收回。报名模拟接口随 Playwright 在任务沙箱内启动。

提交报告时，`reporting-studio` 根据 `out/report/validation.json` 核对交付文件和检查结果。提交站点时，`web-studio` 核对一张通过断言的截图和站点文件版本。页面、样式或数据更新后，重新预览即可生成新凭据。两个 Hook 均按提交路径生效。

## 本地回归

```bash
uv run --project agent --extra mcp --extra examples python -m unittest discover -s agent/tests -p 'test_example_plugins.py' -v
```

默认回归使用脚本化模型和浏览器回执，执行实际的 CLI、MCP 服务、HTTP 接口、工作区 worker 和两个提交 Hook。双插件用例固定触发一次上下文压缩。使用以下命令运行真实 Chromium 交互。

```bash
uv run --project agent --extra examples python -m playwright install chromium
AGENT_COOK_TEST_BROWSER=1 uv run --project agent --extra mcp --extra examples \
  python -m unittest discover -s agent/tests -p 'test_example_plugins.py' -v
```

报名站可在交付目录执行 `python serve.py` 后打开 `http://127.0.0.1:8075/`。双插件站点可在交付的 `out/site/` 对应目录执行 `python -m http.server 8075 --bind 127.0.0.1` 后打开同一地址。
