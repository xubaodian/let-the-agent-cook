import { readFile, stat, writeFile } from "node:fs/promises"
import { join, resolve } from "node:path"
import { stdin, stdout } from "node:process"
import { createInterface } from "node:readline/promises"
import { fileURLToPath } from "node:url"
import { parseArgs } from "node:util"

const site = fileURLToPath(new URL("../", import.meta.url))
const repo = resolve(site, "..")
const sections = new Map([["note", "notes"], ["case", "cases"]])
const usage = [
  "npm run new",
  "npm run new -- note <slug> [--title <标题>] [--description <简介>] [--topic <主题>]",
  "npm run new -- case <slug> [--source-path <仓库内源码路径>] [--body <正文 Markdown 文件>]",
  "",
  "标题、简介、主题、源码路径和正文也可用参数传入。",
  "交互模式会询问缺少的必填项。正文文件的路径相对于调用命令时所在的目录。",
].join("\n")

const templates = {
  note: "<!-- 从一个具体问题写起，可以附上代码和例子。 -->\n",
  case: [
    "## 任务和输入",
    "",
    "<!-- 说明输入来自哪里、任务要交付什么。 -->",
    "",
    "## 实现与验证",
    "",
    "<!-- 记录关键步骤、验收方法和实际结果。 -->",
    "",
  ].join("\n"),
}

async function projectSource(value) {
  const relative = value.replaceAll("\\", "/").replace(/\/$/, "")
  if (/^[A-Za-z]:/.test(relative) || relative.split("/").some((part) => ["", ".", ".."].includes(part))) {
    throw new Error("源码路径应相对于仓库根目录。")
  }
  const info = await stat(join(repo, relative)).catch((error) => {
    if (error.code === "ENOENT" || error.code === "ENOTDIR") {
      throw new Error("仓库中找不到 " + relative)
    }
    throw error
  })
  return info.isDirectory() ? relative + "/" : relative
}

function today() {
  const now = new Date()
  return [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-")
}

async function createContent() {
  const { positionals, values } = parseArgs({
    allowPositionals: true,
    options: {
      help: { type: "boolean", short: "h" },
      title: { type: "string" },
      description: { type: "string" },
      topic: { type: "string" },
      "source-path": { type: "string" },
      body: { type: "string" },
    },
  })
  if (values.help) {
    console.log(usage)
    return
  }
  if (positionals.length > 2) throw new Error("命令最多接受类型和文件名两个位置参数。\n" + usage)

  let prompt
  async function required(value, label, flag) {
    if (value === undefined && !stdin.isTTY) throw new Error("缺少 " + flag + "。\n" + usage)
    if (value === undefined) {
      prompt ??= createInterface({ input: stdin, output: stdout })
      value = await prompt.question(label + "  ")
    }
    const trimmed = value.trim()
    if (!trimmed) throw new Error(label + "不能为空。")
    return trimmed
  }

  try {
    const kind = await required(positionals[0], "内容类型（note 或 case）", "类型 note 或 case")
    const section = sections.get(kind)
    if (!section) throw new Error("内容类型请选择 note 或 case。")
    const slug = await required(positionals[1], "文件名（如 tool-calling）", "<slug>")
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug)) {
      throw new Error("文件名请使用小写英文字母、数字和连字符。")
    }
    const meta = {
      title: await required(values.title, "标题", "--title"),
      description: await required(values.description, "一句简介", "--description"),
      topic: await required(values.topic, "主题（如 Agent Harness 或 AI Infra）", "--topic"),
      date: today(),
    }
    if (kind === "case" || values["source-path"] !== undefined) {
      meta.sourcePath = await projectSource(
        await required(values["source-path"], "源码路径（如 agent/examples/combined-review/run.json）", "--source-path"),
      )
    }

    const body = values.body
      ? await readFile(resolve(process.env.INIT_CWD ?? process.cwd(), values.body), "utf8")
      : templates[kind]
    const fields = Object.entries(meta).map(([key, value]) => key + ": " + JSON.stringify(value))
    const output = ["---", ...fields, "---", "", body.trimEnd(), ""].join("\n")
    const filename = "site/src/content/" + section + "/" + slug + ".md"
    try {
      await writeFile(join(repo, filename), output, { encoding: "utf8", flag: "wx" })
    } catch (error) {
      if (error.code === "EEXIST") throw new Error(filename + " 已存在，请编辑该文件或换一个文件名。")
      throw error
    }
    console.log("已创建 " + filename)
  } finally {
    prompt?.close()
  }
}

createContent().catch((error) => {
  console.error(error.message)
  process.exitCode = 1
})
