import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

function temporaryRepo(t) {
  const root = mkdtempSync(join(tmpdir(), "agent-cook-content-"))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  for (const folder of ["site/scripts", "site/src/content/notes", "site/src/content/cases", "agent"]) {
    mkdirSync(join(root, folder), { recursive: true })
  }
  const script = join(root, "site/scripts/new-content.mjs")
  copyFileSync(new URL("./new-content.mjs", import.meta.url), script)
  return {
    root,
    run(kind, slug, ...args) {
      return spawnSync(process.execPath, [
        script, kind, slug, "--title", "测试：带「引号」的标题", "--description", '含有 "双引号" 的简介',
        "--topic", "Agent Harness", ...args,
      ], { cwd: root, env: { ...process.env, INIT_CWD: root }, encoding: "utf-8" })
    },
  }
}

test("creates Markdown with a supplied body and quoted metadata", (t) => {
  const { root, run } = temporaryRepo(t)
  writeFileSync(join(root, "draft.md"), "这是一段原理笔记。\n", "utf-8")
  const result = run("note", "call-flow", "--body", "draft.md")
  assert.equal(result.status, 0, result.stderr)
  const text = readFileSync(join(root, "site/src/content/notes/call-flow.md"), "utf-8")
  assert.match(text, /^---\ntitle: "测试：带「引号」的标题"/)
  assert.ok(text.includes('description: ' + JSON.stringify('含有 "双引号" 的简介')))
  assert.match(text, /^date: "\d{4}-\d{2}-\d{2}"$/m)
  assert.ok(text.endsWith("这是一段原理笔记。\n"))
})

test("links case source directories and preserves a draft on a duplicate slug", (t) => {
  const { root, run } = temporaryRepo(t)
  const result = run("case", "demo-review", "--source-path", "agent")
  assert.equal(result.status, 0, result.stderr)
  const path = join(root, "site/src/content/cases/demo-review.md")
  const content = readFileSync(path, "utf-8")
  assert.match(content, /^sourcePath: "agent\/"$/m)
  assert.match(content, /^## 实现与验证$/m)

  const duplicate = run("case", "demo-review", "--source-path", "agent/")
  assert.equal(duplicate.status, 1)
  assert.match(duplicate.stderr, /已存在/)
  assert.equal(readFileSync(path, "utf-8"), content)
})

test("rejects invalid slugs and case sources outside or missing from the repo", (t) => {
  const { run } = temporaryRepo(t)
  const invalid = run("note", "../outside")
  assert.equal(invalid.status, 1)
  assert.match(invalid.stderr, /文件名/)
  const escaped = run("case", "outside-repo", "--source-path", "../")
  assert.equal(escaped.status, 1)
  assert.match(escaped.stderr, /相对于仓库根目录/)
  const missing = run("case", "no-source", "--source-path", "absent/")
  assert.equal(missing.status, 1)
  assert.match(missing.stderr, /找不到/)
})
