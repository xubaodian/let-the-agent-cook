# 个人站点

「想明白」写原理和设计取舍，「做出来」记录案例、源码和运行结果。浏览回放、筛选数据和下载文件都可以在静态站完成。

## 本地运行

使用 Node.js 24，从仓库根目录运行。

~~~bash
cd site
npm ci
npm run dev
~~~

打开终端显示的地址。修改 Markdown 后刷新即可预览。

## 新增一篇文章或案例

在 **site/** 运行一个命令，按提示填写内容类型、文件名、标题、简介和主题。案例再提供相对于仓库根目录的源码路径。

~~~bash
npm run new
~~~

脚本会填写日期和 frontmatter，并打印生成的文件。支持用参数录入已有资料。以下示例从 **site/** 目录运行。

~~~bash
npm run new -- note tool-call-basics
npm run new -- case shop-review-notes --source-path agent/examples/combined-review/run.json
~~~

类型、标题、简介和主题也可以用参数一次传齐。用 **--body** 导入已有的 Markdown 正文。

~~~bash
npm run new -- note tool-call-draft \
  --title "一次工具调用如何完成" --description "从工具意图到结果回写" \
  --topic "Agent Harness" --body /path/to/draft.md
~~~

写完后提交 PR。Actions 会校验内容字段和源码链接、运行测试并保留构建产物。合并后发布到站点。

## 写内容

| 目标 | 位置 |
| --- | --- |
| 原理文章 | src/content/notes/ |
| 案例精讲 | src/content/cases/ |
| 回放步骤和产物清单 | src/data/replays/ |
| 可公开的静态产物 | public/replays/ |

每篇文章或案例用 Markdown frontmatter 填写标题、简介、时间和主题。案例可以用 **sourcePath** 指向同仓库的文件或目录。目录路径以斜杠结尾。**setupPath** 和 **command** 提供运行说明，**relatedNotes** 用文章文件名列表关联一篇或多篇原理文章，**replay** 用回放的文件名关联步骤和产物。

例如，开始做 vLLM 服务实验后，在仓库根目录添加 **vllm-serving/**，与 **agent/** 和 **site/** 同级。原理笔记放进文章目录，实验记录放进案例目录，案例的 **sourcePath** 指向该项目。

## 从 Agent Cook 运行导入回放

分享或本地复用一次任务时，保留 Agent Cook 的 JSONL 和同一次运行的 **<run_id>-results/**。以 **--trace /path/run.jsonl** 执行任务可指定记录位置。从 **site/** 检查轨迹中的可选事件。

~~~bash
npm run replay:import -- --trace /path/run.jsonl --inspect
~~~

选几个有解释价值的事件，按打印的 **seq** 填写下面的清单。标题和说明由作者编写。下载和预览文件路径均相对于最终结果目录，由作者按需选择。幻灯片图片可直接使用该次结果目录里的逐页导出图，**slides** 省略时仍可交付 PPTX 下载。

~~~json
{
  "steps": [
    { "seq": 2, "title": "载入任务", "detail": "确认输入范围和交付目标。" }
  ],
  "downloads": [
    { "file": "out/talk.pptx", "label": "下载演讲 PPTX" }
  ],
  "slides": [
    { "file": "out/preview/slide-1.png", "title": "演讲封面" }
  ]
}
~~~

将清单存为 **replay-spec.json**。把示例中的序号和路径替换成自己的值，再导入。

~~~bash
npm run replay:import -- --trace /path/run.jsonl --id xlsx-to-talk --spec /path/replay-spec.json
~~~

结果目录被移动或重命名时，用 **--results /path/to/results** 指定。工具按路径复制清单里点名的文件，并生成 **src/data/replays/xlsx-to-talk.json**。在案例 frontmatter 设置 **replay: xlsx-to-talk** 和 **relatedNotes**。两个栏目会自动建立链接。

导入过程从事件中只取序号、类型和失败状态。提交前检查要公开的步骤说明和文件。原始 JSONL、输入附件、命令和模型输出留在私下，最终站点通过清单展示选中的步骤和产物。

## 刷新周报回放

在安装 LibreOffice 和 Poppler 的 POSIX 机器上，从仓库根目录运行。下面的回归用固定模型响应、库内合成数据和实际 Chromium，生成可以公开的示例产物。

~~~bash
cd agent
uv sync --locked --all-extras
uv run --no-sync python -m playwright install chromium
capture="$(mktemp -d)"
AGENT_COOK_TEST_BROWSER=1 AGENT_COOK_REPLAY_OUTPUT="$capture" \
  uv run --no-sync python -m unittest discover -s tests -p test_example_plugins.py -k two_plugins_share_metrics -v
uv run --no-sync python ../site/scripts/export-weekly-review.py "$capture"
~~~

导出脚本根据事件生成公开步骤，保留实际生成的 PPTX 和通过验证的网页。网页数据内嵌到离线副本，PPTX 由 LibreOffice 渲染成站内预览。原始轨迹留在临时目录。

## 使用 GitHub Pages 发布

1. 在 [仓库 Pages 设置](https://github.com/xubaodian/let-the-agent-cook/settings/pages) 的 **Build and deployment → Source** 选择 **GitHub Actions**。
2. 合并含站点改动的 PR。**Personal site** 工作流会构建和测试，再把通过检查的站点发布到 Pages。若先合并再启用 Pages，在 **Actions → Personal site → Run workflow** 选择 **main** 即可完成首次发布。
3. 发布成功后，从工作流的 **github-pages** 环境打开站点。默认发布地址为 [xubaodian.github.io/let-the-agent-cook/](https://xubaodian.github.io/let-the-agent-cook/)。

PR 检查也会生成 **personal-site-preview**，便于下载产物。站点中的源码链接指向本次构建对应的提交。

需要在本地验证生产构建时，可以从 **site/** 运行以下命令。

~~~bash
npm ci
npm run test:content
npm run check
npm run build
npx playwright install chromium
npm test
~~~
