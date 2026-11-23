import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "agent-cook-replay-"))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  const site = join(root, "site")
  const scripts = join(site, "scripts")
  const results = join(root, "run-123-results")
  const trace = join(root, "trace.jsonl")
  const spec = join(root, "spec.json")
  const document = {
    steps: [
      { seq: 2, title: "读取表格", detail: "读取公开数据。" },
      { seq: 3, title: "修复一次失败", detail: "调整计算脚本。" },
      { seq: 6, title: "保存结果", detail: "通过提交工具交付。" },
    ],
    downloads: [{ file: "out/演讲稿.pptx", label: "演讲稿" }],
    slides: [{ file: "out/slide-1.png", title: "第一页" }],
  }
  const event = (seq, type, data, run_id = "run-123") => ({
    schema_version: 1, seq, type, run_id, root_run_id: "run-123", parent_run_id: run_id === "run-123" ? null : "run-123",
    timestamp: "2026-11-22T07:00:00+00:00", data,
  })
  const events = [
    event(1, "run.started", { model: "private-model" }),
    event(2, "tool.finished", { name: "read_file", tool_call_id: "private-call", is_error: false }),
    event(3, "tool.finished", { name: "run_command", is_error: true }, "child"),
    event(4, "context.compacted", { checkpoint: "private workspace and full context" }),
    event(5, "artifact.created", { artifact: { path: "run-123-artifacts/old-batch/out/演讲稿.pptx" } }),
    event(6, "run.completed", { text: "private final response" }),
  ]
  mkdirSync(scripts, { recursive: true })
  mkdirSync(join(results, "out"), { recursive: true })
  copyFileSync(new URL("./import-replay.mjs", import.meta.url), join(scripts, "import-replay.mjs"))
  writeFileSync(join(results, "out/演讲稿.pptx"), "final file bytes")
  writeFileSync(join(results, "out/slide-1.png"), "preview bytes")
  writeFileSync(join(results, "out/unselected.json"), "{}")

  return {
    site, document, events,
    run(options = []) {
      writeFileSync(trace, events.map((value) => JSON.stringify(value)).join("\n") + "\n")
      writeFileSync(spec, JSON.stringify(document))
      return spawnSync(process.execPath, [
        join(scripts, "import-replay.mjs"),
        "--trace", "trace.jsonl", "--id", "xlsx-to-talk", "--spec", "spec.json", ...options,
      ], { cwd: site, env: { ...process.env, INIT_CWD: root }, encoding: "utf-8" })
    },
  }
}

test("inspects real event shape without printing private event bodies", (t) => {
  const { run, site } = fixture(t)
  const result = run(["--inspect"])
  assert.equal(result.status, 0, result.stderr)
  const list = JSON.parse(result.stdout)
  assert.deepEqual(list[2], { seq: 3, type: "tool.finished", child: true, tool: "run_command", ok: false })
  assert.doesNotMatch(result.stdout, /private|old-batch/)
  assert.equal(existsSync(join(site, "public")), false)
})

test("imports approved steps and only named files from the final result folder", (t) => {
  const { run, site } = fixture(t)
  const result = run()
  assert.equal(result.status, 0, result.stderr)
  const index = join(site, "src/data/replays/xlsx-to-talk.json")
  const text = readFileSync(index, "utf8")
  assert.doesNotMatch(text, /private|old-batch|run-123/)
  const replay = JSON.parse(text)
  assert.deepEqual(replay.steps.map(({ seq }) => seq), [2, 3, 6])
  assert.deepEqual(replay.steps[1].events, ["tool.finished", "执行失败"])
  assert.deepEqual(replay.downloads, [{ path: "replays/xlsx-to-talk/out/演讲稿.pptx", label: "演讲稿" }])
  assert.equal(replay.previews[0].slides[0].path, "replays/xlsx-to-talk/out/slide-1.png")
  const output = join(site, "public/replays/xlsx-to-talk/out")
  assert.equal(readFileSync(join(output, "演讲稿.pptx"), "utf8"), "final file bytes")
  assert.equal(existsSync(join(output, "unselected.json")), false)
  const duplicate = run()
  assert.equal(duplicate.status, 1)
  assert.match(duplicate.stderr, /已存在/)
  assert.equal(readFileSync(index, "utf8"), text)
})

test("rejects invalid references, out-of-bounds paths and missing assets before publishing", (t) => {
  const { run, site, document } = fixture(t)
  document.steps[0].seq = 100
  assert.match(run().stderr, /事件序号/)
  document.steps[0].seq = 2
  document.downloads[0].file = "../private.json"
  assert.match(run().stderr, /相对文件路径/)
  document.downloads[0].file = "out/missing.pptx"
  assert.match(run().stderr, /找不到/)
  assert.equal(existsSync(join(site, "public/replays/xlsx-to-talk")), false)
  assert.equal(existsSync(join(site, "src/data/replays/xlsx-to-talk.json")), false)
})

test("keeps failed runs inspectable and blocks publication of their result files", (t) => {
  const { events, document, run } = fixture(t)
  events.at(-1).type = "run.failed"
  assert.match(run().stderr, /成功完成/)
  assert.equal(run(["--inspect"]).status, 0)
  delete document.downloads
  delete document.slides
  assert.equal(run().status, 0)
})
