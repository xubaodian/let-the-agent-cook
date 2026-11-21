import json
from dataclasses import asdict, replace
from typing import Any
from urllib.parse import urlsplit

from agent_cook.protocol.ui import UiAttachment
from agent_cook.sandbox.limits import MAX_COMMAND_SECONDS, MAX_FIND_RESULTS, MAX_WRITE_BYTES
from agent_cook.sandbox.urls import LOOPBACK_HOSTS
from agent_cook.tools.artifacts import SUBMIT_RESULTS, ArtifactExporter
from agent_cook.tools.public_browser import PublicBrowser
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.spec import ToolContext, ToolDefinition, ToolError, ToolHandler, ToolResult

_PATH = {"type": "string", "minLength": 1, "maxLength": 1024}
_ARGV = {
    "type": "array",
    "minItems": 1,
    "maxItems": 64,
    "items": {"type": "string", "maxLength": 16000},
}
_COUNT = {"type": "integer", "minimum": 1, "maximum": MAX_FIND_RESULTS}
_BOOLEAN = {"type": "boolean"}


def _definition(
    name: str, description: str, properties: dict[str, Any], required: tuple[str, ...] = ()
) -> ToolDefinition:
    return ToolDefinition(name, description, {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    })


_LS = _definition(
    "ls", "List files and directories. Defaults to one level at the workspace root. "
    "Use a workspace-relative directory. Continue with next_cursor.",
    {
        "directory": _PATH,
        "recursive": _BOOLEAN,
        "kind": {"enum": ["all", "file", "directory"]},
        "max_results": _COUNT,
        "cursor": {**_PATH, "description": "The next_cursor from the previous listing with the same options."},
    },
)
_FILES = (
    _definition(
        "read_file", "Read UTF-8 text by line from a workspace-relative path. Use next_line to continue.",
        {
            "path": _PATH,
            "start_line": {"type": "integer", "minimum": 1},
            "line_count": {"type": "integer", "minimum": 1, "maximum": 500},
        }, ("path",),
    ),
    _definition(
        "search_files",
        "Find files by a workspace-relative glob and optionally match literal text with line numbers.",
        {
            "directory": _PATH,
            "glob": {"type": "string", "minLength": 1, "maxLength": 500},
            "query": {"type": "string", "minLength": 1, "maxLength": 1000},
            "case_sensitive": _BOOLEAN,
            "max_results": _COUNT,
        },
    ),
    _definition(
        "write_file", "Write complete UTF-8 text to a workspace file. Parent directories are created.",
        {
            "path": _PATH,
            "content": {"type": "string", "maxLength": MAX_WRITE_BYTES},
            "overwrite": _BOOLEAN,
        }, ("path", "content"),
    ),
    _definition(
        "copy_file", "Copy one workspace file, including binary files. Existing destinations are kept by default.",
        {"source": _PATH, "destination": _PATH, "overwrite": _BOOLEAN},
        ("source", "destination"),
    ),
)

_COMMAND = _definition(
    "run_command",
    "Run a program inside the task sandbox. Use argv with a workspace-relative cwd. Returns exit code and bounded output.",
    {
        "argv": _ARGV,
        "cwd": _PATH,
        "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": MAX_COMMAND_SECONDS},
    }, ("argv",),
)

_BROWSER = _definition(
    "playwright",
    "Inspect a workspace page in Chromium. Use a local HTML path or a loopback URL. "
    "Provide server_argv to start a temporary server for a URL. Screenshots are workspace PNG files.",
    {
        "path": _PATH,
        "url": {"type": "string", "minLength": 1, "maxLength": 2048},
        "server_argv": _ARGV,
        "cwd": _PATH,
        "actions": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "action": {"enum": ["click", "fill", "press", "expect_text"]},
                    "selector": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "value": {"type": "string", "maxLength": 4000},
                },
                "required": ["action", "selector"],
                "additionalProperties": False,
            },
        },
        "screenshot": _PATH,
    },
)

_SUBMIT = _definition(
    SUBMIT_RESULTS, "Export selected result files from the workspace so the rest can expire with it.",
    {"paths": {"type": "array", "minItems": 1, "maxItems": 12, "uniqueItems": True, "items": _PATH}},
    ("paths",),
)


def _operation(name: str) -> ToolHandler:
    async def handle(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        sandbox = await context.get_sandbox()
        result = await sandbox.request(name, arguments)
        return ToolResult(json.dumps(result, ensure_ascii=False))
    return handle


async def _run_command(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    sandbox = await context.get_sandbox()
    result = await sandbox.exec(
        arguments["argv"],
        cwd=arguments.get("cwd", "."),
        timeout_seconds=arguments.get("timeout_seconds", 30),
    )
    return ToolResult(
        json.dumps(asdict(result), ensure_ascii=False),
        is_error=result.timed_out or result.exit_code != 0,
    )


async def _preview(
    arguments: dict[str, Any], context: ToolContext, public_browser: PublicBrowser | None,
) -> ToolResult:
    if ("path" in arguments) == ("url" in arguments):
        raise ToolError("Provide exactly one of path or url for the browser preview.")
    if "path" in arguments and "server_argv" in arguments:
        raise ToolError("Use server_argv with a loopback URL.")
    for action in arguments.get("actions", ()):
        if action["action"] != "click" and "value" not in action:
            raise ToolError(f"The browser action {action['action']} requires value.")
    url = arguments.get("url")
    try:
        public_url = url is not None and urlsplit(url).hostname not in LOOPBACK_HOSTS
    except ValueError as error:
        raise ToolError("Provide a valid browser URL.") from error
    if public_url:
        if public_browser is None:
            raise ToolError("Configure web.browser_origins in the task request to browse public sites.")
        result = await public_browser.read(arguments, context)
    else:
        sandbox = await context.get_sandbox()
        result = await sandbox.request("playwright", arguments, timeout_seconds=65)
    return ToolResult(json.dumps(result, ensure_ascii=False))


def workspace_tools(
    exporter: ArtifactExporter, *, public_browser: PublicBrowser | None = None, native_preview: bool = False,
) -> tuple[ToolRegistry, tuple[str, ...]]:
    registry = ToolRegistry()
    registry.register(_LS, _operation("list_files"))
    for definition in _FILES:
        registry.register(definition, _operation(definition.name))
    registry.register(_COMMAND, _run_command)
    browser = _BROWSER
    if native_preview:
        browser = _definition(
            "playwright", "Preview a local HTML file in offline Chromium. Use workspace-relative paths. "
            "Screenshots are workspace PNG files.",
            {key: _BROWSER.input_schema["properties"][key] for key in ("path", "actions", "screenshot")},
            ("path",),
        )
    elif public_browser is not None:
        browser = replace(_BROWSER, description=(
            _BROWSER.description + " For public page reads, use url and optional actions or screenshot. "
            "Approved HTTPS origins for this task: " + ", ".join(public_browser.origins)
        ))

    async def preview(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return await _preview(arguments, context, public_browser)

    registry.register(browser, preview)

    async def submit(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        sandbox = await context.get_sandbox()
        files = await exporter.submit(sandbox, arguments["paths"])
        artifacts = tuple(file.artifact for file in files)
        return ToolResult(
            json.dumps({"artifacts": [asdict(artifact) for artifact in artifacts]}, ensure_ascii=False),
            artifacts=artifacts,
            ui=UiAttachment("artifacts", {"artifact_ids": [artifact.id for artifact in artifacts]}),
            submitted_files=files,
        )

    registry.register(_SUBMIT, submit)
    return registry, tuple(spec.name for spec in (_LS, *_FILES, _COMMAND, _BROWSER, _SUBMIT))
