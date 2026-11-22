import { defineConfig, devices } from "@playwright/test"

const port = 4321
const prefix = (process.env.SITE_BASE || "").replace(/\/$/, "")
const baseURL = "http://127.0.0.1:" + port + prefix + "/"

export default defineConfig({
  testDir: "./tests",
  use: { baseURL, trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: "npm run preview -- --host 127.0.0.1 --port " + port,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 30000,
  },
  reporter: "list",
})
