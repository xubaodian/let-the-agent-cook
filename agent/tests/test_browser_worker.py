import os
import sys
import unittest
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

@unittest.skipUnless(os.name == "posix", "The workspace worker runs in Linux containers.")
class BrowserWorkerTest(unittest.TestCase):
    def test_static_preview_serves_workspace_assets_and_writes_the_screenshot(self):
        from agent_cook.sandbox.worker.browser import playwright_preview
        from agent_cook.sandbox.worker.workspace import Workspace

        api = MagicMock()
        pw = api.sync_playwright.return_value.__enter__.return_value
        browser = pw.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.title.return_value = "Fixture"
        page.screenshot.return_value = b"\x89PNG\r\n\x1a\nfixture"
        button = MagicMock()
        body = MagicMock()
        body.inner_text.return_value = "Clicked"
        page.locator.side_effect = {"button": button, "body": body}.__getitem__
        api.Error = RuntimeError

        def goto(url, **_):
            page.url = url
            parsed = urlsplit(url)
            conn = HTTPConnection(parsed.hostname, parsed.port, timeout=2)
            try:
                conn.request("GET", parsed.path)
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn(b"<button>", response.read())
                return SimpleNamespace(status=response.status)
            finally:
                conn.close()

        page.goto.side_effect = goto
        with TemporaryDirectory() as directory, patch.dict(sys.modules, {"playwright.sync_api": api}):
            root = Path(directory)
            workspace = Workspace(root)
            workspace.write_bytes("site/index.html", b"<button>Go</button>", overwrite=False)
            result = playwright_preview(
                workspace,
                path="site/index.html",
                actions=[{"action": "click", "selector": "button"}],
                screenshot="shots/preview.png",
            )
            self.assertEqual((root / "shots/preview.png").read_bytes(), page.screenshot.return_value)
            with self.assertRaisesRegex(ValueError, "loopback"):
                playwright_preview(workspace, url="https://example.com/")

        self.assertEqual(result["text"], "Clicked")
        self.assertEqual(result["screenshot"], "shots/preview.png")
        self.assertEqual(result["title"], "Fixture")
        button.click.assert_called_once()
        context.close.assert_called_once()
        browser.close.assert_called_once()
        route = MagicMock()
        route.request.url = "https://example.com/script.js"
        context.route.call_args.args[1](route)
        route.abort.assert_called_once()
