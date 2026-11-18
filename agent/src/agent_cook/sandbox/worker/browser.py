import mimetypes
import os
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import monotonic, sleep
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from agent_cook.sandbox.paths import workspace_path
from agent_cook.sandbox.worker.process import launch, stop
from agent_cook.sandbox.worker.workspace import Workspace

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme, parsed.scheme)
    if (
        scheme not in ("http", "https")
        or parsed.hostname not in _LOOPBACK
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Use an HTTP or HTTPS page on the task sandbox's loopback address.")
    return scheme, parsed.hostname, parsed.port or (443 if scheme == "https" else 80)


@contextmanager
def _serve_files(workspace: Workspace, path: str) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            relative = unquote(urlsplit(self.path).path).removeprefix("/")
            if not relative or relative.endswith("/"):
                relative += "index.html"
            try:
                data = workspace.read_bytes(relative)
            except (OSError, ValueError):
                self.send_error(404)
                return
            mime = mimetypes.guess_type(relative)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args: object) -> None:
            return

    path = workspace_path(path)
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/{quote(path)}"
        finally:
            server.shutdown()
            thread.join(timeout=2)


@contextmanager
def _preview_target(
    workspace: Workspace,
    path: str | None,
    url: str | None,
    server_argv: list[str] | None,
    cwd: str,
) -> Iterator[str]:
    if path is not None:
        if url is not None or server_argv is not None:
            raise ValueError("Choose either a workspace HTML path or a loopback URL.")
        with _serve_files(workspace, path) as target:
            yield target
        return
    if url is None:
        raise ValueError("Provide either path or url for the browser preview.")
    scheme, host, port = _origin(url)
    if scheme != urlsplit(url).scheme:
        raise ValueError("The preview URL must use HTTP or HTTPS.")
    if server_argv is None:
        yield url
        return

    process = launch(server_argv, workspace.directory_path(cwd), capture=False)
    try:
        deadline = monotonic() + 8
        while True:
            if process.poll() is not None:
                raise ValueError(f"The preview server exited with code {process.returncode}.")
            try:
                with socket.create_connection((host, port), timeout=0.25):
                    break
            except OSError:
                if monotonic() >= deadline:
                    raise ValueError("The preview server did not listen on the requested port.") from None
                sleep(0.05)
        yield url
    finally:
        stop(process)


def playwright_preview(
    workspace: Workspace,
    path: str | None = None,
    url: str | None = None,
    server_argv: list[str] | None = None,
    cwd: str = ".",
    actions: list[dict[str, str]] | None = None,
    screenshot: str | None = None,
) -> dict[str, Any]:
    from playwright.sync_api import Error, expect, sync_playwright

    if len(actions or ()) > 8:
        raise ValueError("A browser preview accepts up to eight actions.")
    shot = workspace_path(screenshot) if screenshot is not None else None
    if shot is not None and not shot.lower().endswith(".png"):
        raise ValueError("Choose a .png path for the screenshot.")

    try:
        with _preview_target(workspace, path, url, server_argv, cwd) as target, sync_playwright() as pw:
            allowed_origin = _origin(target)
            executable = os.environ.get("AGENT_COOK_CHROMIUM")
            browser = pw.chromium.launch(executable_path=executable)
            context = browser.new_context(
                service_workers="block",
                accept_downloads=False,
                viewport={"width": 1280, "height": 720},
            )

            def same_origin(address: str) -> bool:
                try:
                    return _origin(address) == allowed_origin
                except ValueError:
                    return False

            def route_request(route: Any) -> None:
                if same_origin(route.request.url):
                    route.continue_()
                else:
                    route.abort()

            def route_socket(route: Any) -> None:
                if same_origin(route.url):
                    route.connect_to_server()
                else:
                    route.close()

            try:
                context.set_default_timeout(3000)
                context.route("**/*", route_request)
                context.route_web_socket("**/*", route_socket)
                page = context.new_page()
                issues: list[str] = []

                def record(message: str) -> None:
                    if len(issues) < 16:
                        issues.append(message[:500])

                page.on("pageerror", lambda error: record(str(error)))
                page.on("console", lambda message: record(message.text) if message.type == "error" else None)
                response = page.goto(target, wait_until="domcontentloaded", timeout=8000)
                if response is not None and response.status >= 400:
                    raise ValueError(f"The preview returned HTTP {response.status}.")
                if not same_origin(page.url):
                    raise ValueError("The page left the selected loopback origin.")
                for step in actions or ():
                    locator = page.locator(step["selector"])
                    action = step["action"]
                    if action == "click":
                        locator.click()
                    elif action == "fill":
                        locator.fill(step["value"])
                    elif action == "press":
                        locator.press(step["value"])
                    elif action == "expect_text":
                        expect(locator).to_contain_text(step["value"], timeout=3000)
                    else:
                        raise ValueError("Use click, fill, press, or expect_text for browser actions.")
                    if not same_origin(page.url):
                        raise ValueError("The page left the selected loopback origin.")
                result = {
                    "url": page.url,
                    "title": page.title()[:300],
                    "text": page.locator("body").inner_text(timeout=3000)[:6000],
                    "page_errors": issues,
                    "screenshot": shot,
                }
                if shot is not None:
                    workspace.write_bytes(shot, page.screenshot(timeout=5000), overwrite=True)
                return result
            finally:
                context.close()
                browser.close()
    except (Error, AssertionError) as error:
        raise ValueError(f"Browser check failed. {str(error)[:450]}") from error
