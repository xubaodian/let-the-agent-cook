---
name: static-site
description: Build and test portable HTML, CSS, and JavaScript websites. Use for landing pages, dashboards, event sites, and interactive prototypes that need responsive layout, local assets, or browser evidence.
---

# Site construction

1. Identify the main visitor action, the content that supports it, and the states the page needs. Build a semantic, responsive entry point with a clear heading and accessible controls.
2. Plan imagery early. A campaign or event page may need a hero or a product illustration. Reuse suitable assets and read `image-assets/SKILL.md` when generating new ones. Place the returned file under the site directory and use a relative URL. Keep copy, controls, and data visualizations in HTML, CSS, and JavaScript.
3. Use verified data for dashboards and render values with the appropriate locale and units. For forms, account for validation, pending, success, and error states. Keep scripts, styles, images, and data local and link them relative to the entry point.
4. Run `playwright` on the HTML path. Choose stable selectors and use `fill`, `click`, and `expect_text` to verify important paths. Check page errors and save an up-to-date PNG when requested. In a server workspace that exposes `server_argv`, use a loopback URL to test a mock API.
5. Rerun the affected checks after changes to markup, styles, scripts, or data. Submit the entry point and all assets required to open the site. Include browser evidence when the delivery calls for it.

The reporting studio supplies example files in `assets/web/registration/` and `assets/web/dashboard/`. For an entry at `out/site/index.html`, the studio's submission hook checks a Playwright text assertion and a PNG under `out/site/` against the version being submitted.
