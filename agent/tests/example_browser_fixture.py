import asyncio
import base64
import json
from http.client import HTTPConnection
from urllib.parse import urlsplit

from agent_cook.sandbox.worker.browser import _preview_target
from agent_cook.sandbox.worker.workspace import Workspace
from test_workspace_tools import WorkerFixture

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/ax2GioAAAAASUVORK5CYII="
)


class ExampleWorker(WorkerFixture):
    """Use the real workspace and HTTP server with optional Chromium execution."""

    def __init__(self, root, *, real_browser):
        super().__init__(root)
        self.real_browser = real_browser

    async def exchange(self, request, timeout):
        payload = json.loads(request)
        if self.real_browser or payload["operation"] != "playwright":
            return await super().exchange(request, timeout)
        result = await asyncio.to_thread(self._browser_observation, payload["arguments"])
        return json.dumps({"version": payload["version"], "ok": True, "result": result}).encode("utf-8")

    def _browser_observation(self, arguments):
        workspace = Workspace(self.root)
        with _preview_target(
            workspace, arguments.get("path"), arguments.get("url"),
            arguments.get("server_argv"), arguments.get("cwd", "."),
        ) as url:
            parsed = urlsplit(url)
            conn = HTTPConnection(parsed.hostname, parsed.port, timeout=3)

            def fetch(path, *, email=None):
                if email is None:
                    conn.request("GET", path)
                else:
                    conn.request("POST", path, json.dumps({"email": email}), {"Content-Type": "application/json"})
                response = conn.getresponse()
                body = response.read().decode("utf-8")
                if response.status != 200:
                    raise AssertionError(f"GET or POST {path} returned {response.status}.")
                return body

            try:
                html = fetch(parsed.path or "/")
                if arguments.get("path"):
                    base = parsed.path.rsplit("/", 1)[0]
                    metrics = json.loads(fetch(f"{base}/metrics.json"))
                    reference = json.loads(fetch(f"{base}/reference.json"))
                    assert 'id="amount"' in html and reference["source"] == "weekly-review/metric-method"
                    selected = metrics
                    amount = f'US${selected["gmv_cents"] / 100:.2f}'.replace(".", ",")
                    for action in arguments["actions"]:
                        if action["action"] == "click":
                            name = action["selector"].removeprefix("[data-category=").removesuffix("]")
                            selected = next(group for group in metrics["categories"] if group["category"] == name)
                            amount = f'US${selected["gmv_cents"] / 100:.2f}'.replace(".", ",")
                        elif action["action"] == "expect_text":
                            assert action["value"] == amount
                    text = amount
                else:
                    copy = json.loads(fetch("/event.json"))
                    assert 'id="status"' in html and 'fetch("/api/register"' in html
                    email, status = "", ""
                    for action in arguments["actions"]:
                        if action["action"] == "fill":
                            email = action["value"]
                        elif action["action"] == "click":
                            if email:
                                status = json.loads(fetch("/api/register", email=email))["message"]
                            else:
                                assert "Enter your email." in html
                                status = "Enter your email."
                        elif action["action"] == "expect_text":
                            assert action["value"] == status
                    text = f'{copy["rules"]} {status}'
            finally:
                conn.close()
        screenshot = arguments["screenshot"]
        workspace.write_bytes(screenshot, _PNG, overwrite=True)
        return {"url": url, "title": "Fixture", "text": text, "page_errors": [], "screenshot": screenshot}
