---
name: browser-qa
description: Plan and execute browser verification for a workspace site or loopback prototype. Use when a task needs interaction assertions, screenshots, regression checks, or evidence for a web deliverable.
---

# Browser QA

1. List the user paths to verify and the visible outcome of each. Cover a representative success path and requested failure or empty states.
2. Call `playwright` with an HTML path for a static preview. For a mock API, use a loopback URL, `server_argv`, and the server's working directory. Prefer selectors that identify a stable field, button, or status.
3. Group each short path into `fill`, `click`, and `expect_text` actions. Each preview accepts up to eight actions and starts a new browser context. Check the returned page errors and save a screenshot when needed for handoff.
4. Make the final assertions and screenshot against the exact files being delivered. Rerun affected checks after editing markup, CSS, scripts, or data.

For the web plugin's example delivery under `out/site/`, capture a PNG under that directory in a passing preview with at least one text assertion. The preview Hook records the checked site files and the submission Hook compares them to the current revision.
