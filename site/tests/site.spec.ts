import { expect, test } from "@playwright/test"
import { readFile } from "node:fs/promises"

test("home, reading lists, article and case link together", async ({ page }) => {
  const broken: string[] = []
  page.on("response", (response) => {
    if (response.status() >= 400) broken.push(response.status() + " " + response.url())
  })
  await page.goto("./")
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Baodian Xu.")
  await expect(page.getByRole("img", { name: /US\$190,00/ })).toBeVisible()
  await expect.poll(() => page.getByRole("img", { name: /US\$190,00/ }).evaluate((image) => (image as HTMLImageElement).naturalWidth)).toBeGreaterThan(0)

  const nav = page.getByRole("navigation")
  await nav.getByRole("link", { name: "想明白" }).click()
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("想明白")
  await expect(page.getByRole("link", { name: /上下文压缩时/ })).toBeVisible()
  await page.getByRole("link", { name: /模型发出 Tool Call/ }).click()
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("模型发出 Tool Call 之后，谁在执行？")
  await expect(page.getByRole("link", { name: /查看实现代码/ })).toHaveAttribute("href", /\/agent\/src\/agent_cook\/core\/loop\.py$/)

  await page.getByRole("link", { name: /看案例回放/ }).click()
  await expect(page).toHaveURL(/\/cases\/weekly-review\/#replay$/)
  await expect(page.getByRole("link", { name: /查看示例源码/ })).toHaveAttribute("href", /\/agent\/examples\/combined-review\/run\.json$/)
  await expect(page.getByText("./cook demo combined-review", { exact: false })).toBeVisible()
  await expect(page.getByRole("link", { name: /了解原理.*模型发出 Tool Call/ })).toHaveAttribute("href", /\/notes\/tool-call\/$/)
  await page.getByRole("link", { name: /了解原理.*上下文压缩时/ }).click()
  await expect(page.getByRole("link", { name: /看案例回放/ })).toHaveAttribute("href", /\/cases\/weekly-review\/#replay$/)

  await nav.getByRole("link", { name: "做出来" }).click()
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("做出来")
  await expect(page.getByRole("link", { name: /一份数据，交付店铺周报和筛选页/ })).toBeVisible()
  expect(broken).toEqual([])
})

test("playback allows navigation, pause, and restart", async ({ page }) => {
  await page.goto("./cases/weekly-review/")
  const detail = page.locator("#replay-step")
  await expect(detail.getByRole("heading")).toHaveText("载入任务")
  await expect(page.getByRole("button", { name: "上一步" })).toBeDisabled()
  await page.getByRole("button", { name: "下一步" }).click()
  await expect(detail.getByRole("heading")).toHaveText("读取订单与口径")

  const checkpoint = page.getByRole("button", { name: "压缩并接续" })
  await checkpoint.click()
  await expect(checkpoint).toHaveAttribute("aria-current", "step")
  await expect(detail.getByText("context.compacted")).toBeVisible()

  await page.clock.install()
  await page.getByRole("button", { name: "播放", exact: true }).click()
  await expect(page.getByRole("button", { name: "暂停" })).toBeVisible()
  await page.clock.runFor(2700)
  await expect(detail.getByRole("heading")).toHaveText("检查浏览器筛选")
  await page.getByRole("button", { name: "暂停" }).click()
  await page.clock.runFor(2700)
  await expect(detail.getByRole("heading")).toHaveText("检查浏览器筛选")

  await page.getByRole("button", { name: "下一步" }).click()
  await expect(detail.getByRole("heading")).toHaveText("提交报告和网页")
  await expect(page.getByRole("button", { name: "下一步" })).toBeDisabled()
  await page.getByRole("button", { name: "播放", exact: true }).click()
  await expect(detail.getByRole("heading")).toHaveText("载入任务")
  await page.getByRole("button", { name: "回到开头" }).click()
  await expect(page.getByRole("button", { name: "播放", exact: true })).toBeVisible()
})

test("offline artifacts preview, filter and download", async ({ page, context }) => {
  const externalRequests: string[] = []
  await context.route(/^https?:/, async (route) => {
    if (new URL(route.request().url()).hostname === "127.0.0.1") {
      await route.continue()
    } else {
      externalRequests.push(route.request().url())
      await route.abort()
    }
  })
  const errors: string[] = []
  page.on("pageerror", (error) => errors.push(error.message))
  await page.goto("./cases/weekly-review/#outputs")
  await expect(page.getByRole("img", { name: "Tinjauan kinerja mingguan" })).toBeVisible()
  await page.getByRole("button", { name: "下一页", exact: true }).click()
  await expect(page.getByRole("img", { name: "Nilai pesanan per kategori" })).toBeVisible()
  await page.getByRole("button", { name: "上一页", exact: true }).click()
  await expect(page.getByRole("img", { name: "Tinjauan kinerja mingguan" })).toBeVisible()

  const [deck] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("link", { name: /下载 PPTX/ }).click(),
  ])
  expect(deck.suggestedFilename()).toBe("review.pptx")
  const deckBytes = await readFile(await deck.path())
  expect(deckBytes.subarray(0, 4)).toEqual(Buffer.from([0x50, 0x4b, 3, 4]))
  expect(deckBytes.includes(Buffer.from("ppt/slides/slide1.xml"))).toBe(true)

  const [metrics] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("link", { name: /指标 JSON/ }).click(),
  ])
  expect(JSON.parse(await readFile(await metrics.path(), "utf8")).gmv_cents).toBe(55000)

  await page.getByRole("button", { name: "筛选网页", exact: true }).click()
  const frame = page.frameLocator('iframe[title="店铺周报筛选网页"]')
  await expect(frame.getByRole("status")).toHaveText("US$550,00")
  await frame.getByRole("button", { name: "Mode", exact: true }).click()
  await expect(frame.getByRole("status")).toHaveText("US$190,00")
  await frame.getByRole("button", { name: "Semua kategori" }).click()
  await expect(frame.getByRole("status")).toHaveText("US$550,00")
  await expect(page.getByRole("link", { name: /单独打开网页/ })).toHaveAttribute("href", /\/dashboard\.html$/)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  expect(externalRequests).toEqual([])
  expect(errors).toEqual([])
})
