import { copyFile, link, mkdir, mkdtemp, readFile, realpath, rm, stat, writeFile } from "node:fs/promises"
import { dirname, extname, isAbsolute, join, relative, resolve, sep } from "node:path"
import { fileURLToPath } from "node:url"
import { parseArgs } from "node:util"

const site = fileURLToPath(new URL("../", import.meta.url))
const images = new Set([".png", ".jpg", ".jpeg", ".webp"])
const downloads = new Set([".pptx", ".pdf", ".xlsx", ".csv", ".json", ".md", ".txt", ...images])
const milestones = new Set([
  "extensions.loaded", "run.started", "model.finished", "tool.finished", "hook.finished",
  "context.compacted", "context.compaction_failed", "artifact.created", "ui.attached",
  "run.completed", "run.failed", "run.cancelled",
])
const usage = [
  "npm run replay:import -- --trace <run.jsonl> --inspect",
  "npm run replay:import -- --trace <run.jsonl> --id <slug> --spec <replay-spec.json> [--results <目录>]",
  "",
  "清单提供公开的步骤标题、说明和要发布的文件。省略 --results 时使用轨迹旁的 <run_id>-results/。",
].join("\n")

function parseTrace(text) {
  let previous = 0
  return text.split(/\r?\n/).flatMap((line, index) => {
    if (!line.trim()) return []
    let event
    try {
      event = JSON.parse(line)
    } catch {
      throw new Error("JSONL 第 " + (index + 1) + " 行不是有效的 JSON。")
    }
    if (event?.schema_version !== 1 || !Number.isSafeInteger(event.seq) || event.seq <= previous ||
        typeof event.type !== "string" || typeof event.root_run_id !== "string" ||
        typeof event.run_id !== "string" || !event.data || typeof event.data !== "object" || Array.isArray(event.data)) {
      throw new Error("JSONL 第 " + (index + 1) + " 行的事件版本、序号或字段不正确。")
    }
    previous = event.seq
    return [event]
  })
}

function describe(event) {
  const summary = { seq: event.seq, type: event.type }
  if (event.run_id !== event.root_run_id) summary.child = true
  if (event.type === "tool.finished" && typeof event.data.name === "string") summary.tool = event.data.name
  if (typeof event.data.is_error === "boolean") summary.ok = !event.data.is_error
  return summary
}

function nonempty(value, name) {
  if (typeof value !== "string" || !value.trim()) throw new Error("缺少 " + name + "。")
  return value.trim()
}

function entries(value, field) {
  if (value === undefined) return []
  if (!Array.isArray(value)) throw new Error(field + " 应为数组。")
  return value
}

function filePath(value, allowed) {
  const path = nonempty(value, "文件路径").replaceAll("\\", "/")
  if (path.split("/").some((part) => !/^[\p{L}\p{N}_-][\p{L}\p{N}_. -]*$/u.test(part))) {
    throw new Error("使用结果目录内的相对文件路径，每个目录和文件名须以字母、数字、下划线或连字符开头。")
  }
  if (!allowed.has(extname(path).toLowerCase())) throw new Error("不支持发布此文件类型 " + path)
  return path
}

async function resultFile(root, path) {
  const candidate = await realpath(join(root, path)).catch((error) => {
    if (error.code === "ENOENT" || error.code === "ENOTDIR") throw new Error("结果目录中找不到 " + path)
    throw error
  })
  const local = relative(root, candidate)
  if (isAbsolute(local) || local === ".." || local.startsWith(".." + sep) || !(await stat(candidate)).isFile()) {
    throw new Error("结果文件必须是目录内的普通文件 " + path)
  }
  return candidate
}

function publicReplay(id, spec, events) {
  if (!spec || typeof spec !== "object" || Array.isArray(spec)) throw new Error("回放清单应为 JSON 对象。")
  const bySequence = new Map(events.filter((event) => milestones.has(event.type)).map((event) => [event.seq, event]))
  let previous = 0
  const steps = entries(spec.steps, "steps").map((step) => {
    const event = bySequence.get(step?.seq)
    if (!event || event.seq <= previous) throw new Error("步骤必须引用递增的事件序号 " + step?.seq)
    previous = event.seq
    return {
      id: "event-" + event.seq,
      seq: event.seq,
      title: nonempty(step.title, "步骤标题"),
      detail: nonempty(step.detail, "步骤说明"),
      events: event.data.is_error === true ? [event.type, "执行失败"] : [event.type],
    }
  })
  if (!steps.length) throw new Error("清单至少需要一个 steps 项。")

  const prefix = "replays/" + id + "/"
  const files = new Set()
  const assetPath = (path, allowed) => {
    const name = filePath(path, allowed)
    files.add(name)
    return prefix + name
  }
  const slideImages = entries(spec.slides, "slides").map((slide) => ({
    path: assetPath(slide?.file, images),
    title: nonempty(slide?.title, "幻灯片标题"),
  }))
  return {
    files,
    document: {
      version: 1,
      steps,
      previews: slideImages.length ? [{ id: "deck", kind: "slides", label: "PowerPoint", slides: slideImages }] : [],
      downloads: entries(spec.downloads, "downloads").map((download) => ({
        path: assetPath(download?.file, downloads),
        label: nonempty(download?.label, "下载名称"),
      })),
    },
  }
}

async function publish(id, { document, files }, results) {
  const sources = new Map()
  if (files.size) {
    const root = await realpath(results)
    for (const path of files) sources.set(path, await resultFile(root, path))
  }
  const dataDir = join(site, "src/data/replays")
  const manifest = join(dataDir, id + ".json")
  const assetDir = join(site, "public/replays", id)
  await mkdir(dirname(assetDir), { recursive: true })
  await mkdir(dataDir, { recursive: true })
  const staging = await mkdtemp(join(dataDir, ".replay-"))
  try {
    await mkdir(assetDir)
    try {
      for (const [path, source] of sources) {
        const destination = join(assetDir, path)
        await mkdir(dirname(destination), { recursive: true })
        await copyFile(source, destination)
      }
      const temporary = join(staging, "replay.json")
      await writeFile(temporary, JSON.stringify(document, null, 2) + "\n")
      await link(temporary, manifest)
    } catch (error) {
      await rm(assetDir, { recursive: true, force: true })
      throw error
    }
  } catch (error) {
    if (error.code === "EEXIST") throw new Error("回放 " + id + " 已存在。请编辑该回放或换一个 ID。")
    throw error
  } finally {
    await rm(staging, { recursive: true, force: true })
  }
}

async function main() {
  const { values } = parseArgs({
    options: {
      trace: { type: "string" },
      id: { type: "string" },
      spec: { type: "string" },
      results: { type: "string" },
      inspect: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  })
  if (values.help) {
    console.log(usage)
    return
  }
  const cwd = process.env.INIT_CWD ?? process.cwd()
  const trace = resolve(cwd, nonempty(values.trace, "--trace"))
  const events = parseTrace(await readFile(trace, "utf8"))
  const root = events.find((event) => event.type === "run.started" && event.run_id === event.root_run_id)?.run_id
  if (!root || !/^[a-zA-Z0-9_-]+$/.test(root) || events.some((event) => event.root_run_id !== root)) {
    throw new Error("请提供对应一次 Agent Cook 运行的完整 JSONL。")
  }
  if (values.inspect) {
    console.log(JSON.stringify(events.filter((event) => milestones.has(event.type)).map(describe), null, 2))
    return
  }
  const id = nonempty(values.id, "--id")
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id)) throw new Error("回放 ID 使用小写字母、数字和连字符。")
  const spec = JSON.parse(await readFile(resolve(cwd, nonempty(values.spec, "--spec")), "utf8"))
  const replay = publicReplay(id, spec, events)
  const final = events.findLast((event) => event.run_id === root)
  if (replay.files.size && final?.type !== "run.completed") {
    throw new Error("有最终产物的公开回放需要一次成功完成的运行。")
  }
  const resultDir = values.results ? resolve(cwd, values.results) : join(dirname(trace), root + "-results")
  await publish(id, replay, resultDir)
  console.log("已导入 src/data/replays/" + id + ".json。在案例中设置 replay: " + id)
}

main().catch((error) => {
  console.error(error.message)
  process.exitCode = 1
})
