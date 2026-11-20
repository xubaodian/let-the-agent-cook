import asyncio
from contextlib import AsyncExitStack
from typing import Any

from agent_cook.sandbox.interface import Sandbox, SandboxFactory
from agent_cook.sandbox.paths import workspace_path
from agent_cook.sandbox.urls import public_https_origin
from agent_cook.tools.spec import ToolContext, ToolError


class PublicBrowser:
    """Run public page reads in a separate, lazily acquired sandbox."""

    def __init__(
        self, factory: SandboxFactory, resources: AsyncExitStack, run_id: str, origins: tuple[str, ...],
    ) -> None:
        self.origins = origins
        self._factory = factory
        self._resources = resources
        self._run_id = run_id
        self._sandbox: Sandbox | None = None
        self._lock = asyncio.Lock()

    async def read(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        try:
            origin = public_https_origin(arguments["url"])
            screenshot = workspace_path(arguments["screenshot"]) if "screenshot" in arguments else None
        except ValueError as error:
            raise ToolError(str(error)) from error
        if origin not in self.origins:
            raise ToolError("Add this HTTPS origin to the task's web.browser_origins before reading it.")
        if "server_argv" in arguments or "cwd" in arguments:
            raise ToolError("Public browser reads use url, actions and screenshot. Use server_argv and cwd for loopback previews.")
        if screenshot is not None and not screenshot.lower().endswith(".png"):
            raise ToolError("Choose a .png path for the screenshot.")

        async with self._lock:
            if self._sandbox is None:
                self._sandbox = await self._resources.enter_async_context(self._factory.open(f"{self._run_id}-web"))
            result = await self._sandbox.request("playwright_public", {
                "url": arguments["url"],
                "browser_origins": list(self.origins),
                "actions": arguments.get("actions"),
                "screenshot": screenshot,
            }, timeout_seconds=65)
            if screenshot is not None:
                contents = await self._sandbox.get_file(screenshot)
                await (await context.get_sandbox()).put_file(screenshot, contents, overwrite=True)
        if context.events is not None:
            context.events.emit("browser.page_read", url=result["url"], title=result["title"], screenshot=screenshot)
        return result
