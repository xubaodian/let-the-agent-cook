# 个人站点

「想明白」写原理和设计取舍，「做出来」记录案例、源码和运行结果。浏览回放、筛选数据和下载文件都可以在静态站完成。

## 本地运行

使用 Node.js 24，从仓库根目录运行。

~~~bash
cd site
npm ci
npm run dev
~~~

打开终端显示的地址。测试生产构建时，先执行下面的命令。

~~~bash
npm run check
npm run build
npx playwright install chromium
npm test
~~~

## 写内容

| 目标 | 位置 |
| --- | --- |
| 原理文章 | src/content/notes/ |
| 案例精讲 | src/content/cases/ |
| 回放步骤和产物清单 | src/data/replays/ |
| 可公开的静态产物 | public/replays/ |

每篇文章或案例用 Markdown frontmatter 填写标题、简介、时间和主题。案例可以用 **sourcePath** 指向同仓库的文件或目录。目录路径以斜杠结尾。**setupPath** 和 **command** 提供运行说明，**relatedNote** 用文章的文件名关联原理文章，**replay** 用回放的文件名关联步骤和产物。

例如，开始做 vLLM 服务实验后，在仓库根目录添加 **vllm-serving/**，与 **agent/** 和 **site/** 同级。原理笔记放进文章目录，实验记录放进案例目录，案例的 **sourcePath** 指向该项目。

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

## 构建与发布

Personal site 工作流用 GitHub Pages 项目路径构建和测试，并保留 **personal-site-preview** 构建产物。要在现有仓库发布，先在仓库的 Settings → Pages 选择 GitHub Actions，再在主分支手动运行工作流并选中 **deploy**。源码链接固定到本次构建的提交。
