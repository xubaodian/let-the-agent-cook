# Let the Agent Cook

Agent Cook 是一个接入 OpenRouter 的终端助手。你可以用它对话、整理本地文件、生成报告和制作网页。本地 CLI 无需 Docker。

## 安装与启动

解压源码后，在源码根目录安装。

| 系统 | 首次安装 | 再次启动 |
| --- | --- | --- |
| macOS / Linux | `./install` | `./cook` |
| Windows | 双击 `install.cmd` | 双击 `cook.cmd`，或运行 `.\cook.cmd` |

安装脚本会准备 Python 和依赖，完成后打开 CLI。Ubuntu 源码安装需要额外安装 Bubblewrap（`sudo apt install bubblewrap`）。原生安装包发布后可在 Releases 下载。 [安装指南](docs/install.md) · [GitHub Releases](https://github.com/xubaodian/let-the-agent-cook/releases)

首次启动时，在浏览器授权 OpenRouter 或粘贴已有的 API Key，再选择支持工具调用的模型。Exa 用于网页检索，可按 Enter 跳过。 [配置说明](docs/install.md#配置)

## CLI 用法

在源码目录运行时，把下列命令中的 `agent-cook` 换成 `./cook`（macOS / Linux）或 `.\cook.cmd`（Windows）。

| 命令 | 用途 |
| --- | --- |
| `agent-cook` | 打开交互会话，直接输入任务。 |
| `agent-cook exec "任务"` | 执行一次任务。用 `--input "路径"` 添加文件或文件夹，可重复传入。 |
| `agent-cook resume --last` | 继续当前工作目录的最近一次会话。 |
| `agent-cook doctor` | 检查模型配置和本机沙箱。 |

交互时用 `@路径` 或 `@"带空格的路径"` 选择文件或文件夹。Agent 处理副本，交付后会打印结果目录的路径。用 `/open` 打开当前会话的结果，`/model` 查看或切换模型，`/help` 查看其他命令。

## 使用示例

### 写一封邮件

```sh
agent-cook exec "用英文写一封简短邮件，邀请同事参加周五的项目复盘，附上三项议程。"
```

### 整理本地销售数据

在交互 CLI 中输入 `/example`，生成包含销售 CSV 的样例目录。把终端打印的整条任务粘贴回 CLI，完成后输入 `/open` 查看提交的销售简报。

### 生成网页并截图

在交互 CLI 中输入下面的任务。完成后用 `/open` 查看 `index.html` 和 `proof.png`。

```text
做一个自包含的活动介绍页 index.html。用 Playwright 检查页面标题并保存 proof.png，交付网页和截图。
```

[更多示例](examples/README.md) · [工作区工具](docs/workspace-tools.md) · [插件扩展](docs/plugins.md) · [HTTP 服务](docs/server.md)
