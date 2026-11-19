---
name: static-site
description: Build a shareable HTML, CSS, and JavaScript site with useful interactions, local assets, and a clear entry point. Use for dashboards, event pages, microsites, and portable prototypes.
---

# Static site

1. Identify the user's main action, the data or copy that supports it, and the states visitors need to see. Build a semantic HTML entry point with responsive styles.
2. Keep links to scripts, images, and data relative. Prefer checked-in or generated local assets so the site can run on the sandbox's loopback server and travel as a bundle.
3. For a data dashboard, read a verified JSON file and render text with DOM APIs. For a form, handle validation, pending, success, and error states. Keep the test endpoint and its inputs explicit.
4. Preview the site through `playwright` and follow `browser-qa` when the run includes that Skill. Export the entry point, required assets, and any mock service needed to exercise the prototype.

The web plugin includes `assets/web/registration/` for an event page and `assets/web/dashboard/index.html` for a filterable review. The dashboard reads `metrics.json` and `reference.json` next to its entry point.
