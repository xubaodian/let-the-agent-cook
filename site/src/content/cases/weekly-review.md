---
title: 一份数据，交付店铺周报和筛选页
description: 用两个 Studio 处理示例订单，把同一份指标交付为可编辑 PowerPoint 和可以筛选的网页。
date: 2026-11-22
topic: Agent Harness
sourcePath: agent/examples/combined-review/run.json
setupPath: agent/README.md
command: |
  cd agent
  ./cook demo combined-review
replay: weekly-review
relatedNote: tool-call
---

## 任务怎么拆

示例取自仓库中的合成订单和类目基准。数据服务返回两页订单，资料服务提供印尼语 GMV 口径。任务先去重并保留缺失金额，再从同一份指标分别生成印尼语周报和网页，避免两份交付引用不同的数字。

## 怎么判断完成

报告和站点使用相同的指标 JSON。样例去重后有 6 个订单，其中 5 个有金额，GMV 合计为 **US$550,00**。在网页点 **Mode** 时，金额应切换为 **US$190,00**。提交时两个 Studio 的 Hook 检查报告和站点的交付文件。

回放来自仓库的组合回归用例。用例以固定模型响应触发上下文压缩，实际运行 CLI、MCP、文件生成和 Chromium 检查。公开记录摘录了这些步骤，产物可以直接打开。在本地自由运行此任务时，需要在 Agent Cook 中配置 OpenRouter。
