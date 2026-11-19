import asyncio
import json
import re
from collections.abc import Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, ValidationError

from agent_cook.sandbox.interface import SandboxError
from agent_cook.sandbox.paths import workspace_path
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.spec import ToolContext, ToolDefinition, ToolHandler, ToolResult

MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
_TIMEOUT_SECONDS = 15.0
_STRING = {"type": "string", "minLength": 1}
_STRINGS = {"type": "object", "additionalProperties": {"type": "string"}}
_SERVER_VALIDATORS = {
    "stdio": Draft202012Validator({
        "type": "object",
        "required": ["type", "command"],
        "properties": {
            "type": {"const": "stdio"}, "command": _STRING,
            "args": {"type": "array", "items": {"type": "string"}},
            "env": _STRINGS, "cwd": _STRING,
        },
        "additionalProperties": False,
    }),
    "streamable-http": Draft202012Validator({
        "type": "object",
        "required": ["type", "url"],
        "properties": {"type": {"const": "streamable-http"}, "url": _STRING, "headers": _STRINGS},
        "additionalProperties": False,
    }),
}


@dataclass(frozen=True, slots=True)
class McpServer:
    name: str
    root: Path
    config: dict[str, Any]


def read_servers(root: Path, document: dict) -> tuple[McpServer, ...]:
    servers = document.get("mcpServers")
    if document.get("$schema") != MCP_SCHEMA or not isinstance(servers, dict):
        raise ValueError("mcp.json must declare the Agent Plugins MCP schema and mcpServers.")
    result = []
    for name, config in servers.items():
        if not name.strip() or not isinstance(config, dict):
            raise ValueError("An MCP server requires a name and configuration object.")
        transport = config.get("type")
        validator = _SERVER_VALIDATORS.get(transport) if isinstance(transport, str) else None
        if validator is None:
            raise ValueError(f"MCP server '{name}' must use stdio or streamable-http.")
        try:
            validator.validate(config)
        except ValidationError as error:
            raise ValueError(f"Invalid configuration for MCP server '{name}' at {error.json_path}.") from error
        if transport == "streamable-http":
            endpoint = urlsplit(config["url"])
            if endpoint.scheme not in ("http", "https") or not endpoint.hostname or endpoint.username is not None:
                raise ValueError(f"MCP server '{name}' requires an HTTP(S) URL without embedded credentials.")
        elif {"PLUGIN_ROOT", "PLUGIN_DATA"} & config.get("env", {}).keys():
            raise ValueError(f"MCP server '{name}' cannot set reserved plugin environment variables.")
        result.append(McpServer(name, root, config))
    return tuple(result)


def _package_path(root: Path, relative: str) -> Path:
    path = (root / workspace_path(relative, allow_root=True)).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError("An MCP package path must remain inside the plugin.")
    return path


def _stdio_paths(root: Path, config: dict) -> tuple[str, Path]:
    command = config["command"]
    if command.startswith("./"):
        script = _package_path(root, command)
        if not script.is_file():
            raise ValueError("An MCP executable must be a file.")
        command = str(script)
    elif "/" in command or "\\" in command:
        raise ValueError("Use an MCP executable on PATH or a plugin-relative executable starting with ./")
    cwd = config.get("cwd", ".")
    if cwd == "${PLUGIN_ROOT}":
        cwd = "."
    elif cwd.startswith("${PLUGIN_ROOT}/"):
        cwd = cwd.removeprefix("${PLUGIN_ROOT}/")
    path = _package_path(root, cwd)
    if not path.is_dir():
        raise ValueError("An MCP working directory must be a directory.")
    return command, path


def _tool_name(server: str, tool: str) -> str:
    original = f"mcp__{server}__{tool}"
    alias = re.sub(r"[^A-Za-z0-9_-]", "_", original)
    if alias != original or len(alias) > 64:
        alias = f"{alias[:50]}__{sha256(original.encode()).hexdigest()[:12]}"
    return alias


def _tool_selections(servers: Sequence[McpServer], selected: Sequence[str]) -> dict[str, set[str]]:
    names = {server.name for server in servers}
    selections: dict[str, set[str]] = {}
    for selector in selected:
        if "/" not in selector or selector.startswith("/") or selector.endswith("/"):
            raise SandboxError(f"Use server/tool for MCP tool selection, got '{selector}'.")
        matches = [name for name in names if selector.startswith(f"{name}/")]
        if not matches:
            raise SandboxError(f"MCP tool '{selector}' refers to an unknown server.")
        if len(matches) > 1:
            raise SandboxError(f"MCP tool '{selector}' matches more than one server name.")
        server, = matches
        selections.setdefault(server, set()).add(selector[len(server) + 1:])
    return selections


async def register_mcp_tools(
    stack: AsyncExitStack, registry: ToolRegistry, servers: Sequence[McpServer],
    *, selected: Sequence[str] | None = None,
) -> tuple[str, ...]:
    selections = _tool_selections(servers, selected) if selected is not None else None
    active_servers = [server for server in servers if selections is None or server.name in selections]
    if not active_servers:
        return ()

    from httpx import AsyncClient
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    names = []
    for server in active_servers:
        try:
            config = server.config
            if config["type"] == "stdio":
                command, cwd = _stdio_paths(server.root, config)
                params = StdioServerParameters(
                    command=command, cwd=cwd, args=config.get("args", []),
                    env={**config.get("env", {}), "PLUGIN_ROOT": str(server.root)},
                )
                streams = await stack.enter_async_context(stdio_client(params))
            else:
                http = await stack.enter_async_context(AsyncClient(
                    headers=config.get("headers"), timeout=_TIMEOUT_SECONDS, follow_redirects=False,
                ))
                streams = await stack.enter_async_context(streamable_http_client(config["url"], http_client=http))
            session = await stack.enter_async_context(ClientSession(
                streams[0], streams[1], read_timeout_seconds=timedelta(seconds=_TIMEOUT_SECONDS),
            ))
            wanted = selections[server.name] if selections is not None else None
            async with asyncio.timeout(_TIMEOUT_SECONDS):
                names.extend(await _discover_tools(session, registry, server.name, wanted))
        except SandboxError:
            raise
        except Exception as error:
            raise SandboxError(f"Could not load MCP tools from '{server.name}' ({type(error).__name__}).") from error
    return tuple(names)


async def _discover_tools(
    session: Any, registry: ToolRegistry, server: str, wanted: set[str] | None,
) -> list[str]:
    from mcp.types import PaginatedRequestParams

    initialized = await session.initialize()
    tools = []
    if initialized.capabilities.tools is not None:
        cursor = None
        seen_cursors = set()
        while True:
            page = await session.list_tools(params=PaginatedRequestParams(cursor=cursor))
            tools.extend(tool for tool in page.tools if wanted is None or tool.name in wanted)
            cursor = page.nextCursor
            if cursor is None:
                break
            if cursor in seen_cursors:
                raise ValueError("The MCP server repeated a tool discovery cursor.")
            seen_cursors.add(cursor)

    if wanted is not None and (missing := wanted - {tool.name for tool in tools}):
        refs = ", ".join(f"{server}/{name}" for name in sorted(missing))
        raise SandboxError(f"MCP tools not found: {refs}.")

    names = []
    for tool in tools:
        name = _tool_name(server, tool.name)
        registry.register(
            ToolDefinition(name, tool.description or tool.name, tool.inputSchema),
            _handler(session, tool.name),
        )
        names.append(name)
    return names


def _handler(session: Any, name: str) -> ToolHandler:
    async def invoke(arguments: dict[str, Any], _context: ToolContext) -> ToolResult:
        result = await session.call_tool(name, arguments)
        if result.structuredContent is not None:
            content = json.dumps(result.structuredContent, ensure_ascii=False)
        else:
            content = "\n".join(block.text for block in result.content if block.type == "text")
        return ToolResult(content or "The MCP tool returned no text or structured content.", is_error=result.isError)
    return invoke
