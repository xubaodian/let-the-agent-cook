"""Render workspace HTML inside the native sandbox without a listening socket."""

import json
import mimetypes
import os
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

from playwright.sync_api import Error, sync_playwright

_ORIGIN = "http://agent-cook-workspace.invalid"
_LIMIT = 16 * 1024 * 1024


def open_browser(playwright, request):
    if "windows_launcher" not in request:
        switches = ["--single-process", "--disable-gpu"] if sys.platform == "darwin" else []
        return playwright.chromium.launch(args=switches)
    from playwright._impl._driver import compute_driver_executable

    # The driver creates its pipes on the host. The launcher binds the same pipes
    # to headless Chromium inside an AppContainer, without opening a CDP port.
    _, cli = compute_driver_executable()
    with (Path(cli).parent / "browsers.json").open(encoding="utf-8") as file:
        browsers = json.load(file)["browsers"]
    revision = next(browser["revision"] for browser in browsers if browser["name"] == "chromium-headless-shell")
    executable = next((Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]) / f"chromium_headless_shell-{revision}").glob(
        "*/chrome-headless-shell.exe",
    ))
    launcher, *arguments = request["windows_launcher"]
    return playwright.chromium.launch(
        executable_path=launcher, ignore_default_args=True, args=[
            *arguments, "__chromium_appcontainer", request["windows_scratch"], str(executable),
        ],
    )


def preview(request: dict) -> dict:
    workspace = Path.cwd().resolve()
    with sync_playwright() as playwright:
        browser = open_browser(playwright, request)
        context = browser.new_context(service_workers="block", accept_downloads=False)

        def asset(route):
            url = urlsplit(route.request.url)
            path = PurePosixPath(unquote(url.path).lstrip("/"))
            if (f"{url.scheme}://{url.netloc}" != _ORIGIN or route.request.method != "GET"
                    or ".." in path.parts or any(":" in part or "\\" in part for part in path.parts)):
                route.abort()
                return
            try:
                file = workspace.joinpath(*path.parts).resolve(strict=True)
                if not file.is_relative_to(workspace) or not file.is_file() or file.stat().st_size > _LIMIT:
                    raise ValueError("Unsupported browser asset.")
                data = file.read_bytes()
            except (OSError, ValueError):
                route.fulfill(status=404, body=b"")
            else:
                route.fulfill(body=data, content_type=mimetypes.guess_type(file.name)[0] or "application/octet-stream")

        context.set_default_timeout(3000)
        context.route("**/*", asset)
        context.route_web_socket("**/*", lambda socket: socket.close())
        page = context.new_page()
        errors = []

        def record_error(error):
            if len(errors) < 10:
                errors.append(str(error)[:512])

        page.on("pageerror", record_error)
        page.goto(f"{_ORIGIN}/{quote(request['path'])}", wait_until="domcontentloaded", timeout=8000)
        for action in request.get("actions") or ():
            target = page.locator(action["selector"])
            if action["action"] == "click":
                target.click()
            elif action["action"] == "fill":
                target.fill(action["value"])
            elif action["action"] == "press":
                target.press(action["value"])
            elif action["action"] == "expect_text":
                from playwright.sync_api import expect

                expect(target).to_contain_text(action["value"])
        shot = request.get("screenshot")
        if shot:
            destination = workspace / shot
            if not destination.resolve().is_relative_to(workspace):
                raise ValueError("Keep the screenshot inside the workspace.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=destination)
        return {
            "url": page.url, "title": page.title(), "text": page.locator("body").inner_text()[:12000],
            "page_errors": errors, "screenshot": shot,
        }


if __name__ == "__main__":
    request = json.load(sys.stdin.buffer)
    try:
        sys.stdout.buffer.write(json.dumps(preview(request), ensure_ascii=False).encode("utf-8"))
    except Error as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from None
