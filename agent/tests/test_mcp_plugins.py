import importlib.util
import json
import os
import sys
import unittest
from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from agent_cook.extensions.mcp import MCP_SCHEMA, read_servers, register_mcp_tools
from agent_cook.sandbox.interface import SandboxError
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.spec import ToolCall, ToolContext


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Install the optional MCP dependencies.")
class McpPluginTest(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_server_discovery_and_tool_call(self):
        from agent_cook.extensions.plugins import PLUGIN_SCHEMA, load_plugins

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "plugin.json").write_text(json.dumps({"$schema": PLUGIN_SCHEMA, "name": "test-mcp"}))
            (root / "mcp.json").write_text(json.dumps({
                "$schema": MCP_SCHEMA,
                "mcpServers": {
                    "test": {
                        "type": "stdio",
                        "command": Path(sys.executable).name,
                        "args": ["server.py"],
                        "env": {"PATH": os.pathsep.join((str(Path(sys.executable).parent), os.environ.get("PATH", "")))},
                    },
                    "unused": {"type": "stdio", "command": "./unavailable-server"},
                },
            }))
            (root / "server.py").write_text(
                "from mcp.server.fastmcp import FastMCP\n"
                "server = FastMCP('test-server')\n"
                "@server.tool()\n"
                "def summarize(value: int) -> dict[str, int]:\n"
                "    return {'value': value}\n"
                "@server.tool()\n"
                "def details() -> str:\n"
                "    return 'Other tool'\n"
                "server.run(transport='stdio')\n",
                encoding="utf-8",
            )
            plugin, = load_plugins((root,))
            registry = ToolRegistry()
            async with AsyncExitStack() as stack:
                names = await register_mcp_tools(
                    stack, registry, plugin.mcp_servers, selected=("test/summarize",),
                )
                self.assertEqual(names, ("mcp__test__summarize",))
                with self.assertRaisesRegex(ValueError, "no registered handler"):
                    registry.definitions(("mcp__test__details",))
                result = await registry.invoke(
                    ToolCall("mcp-call", names[0], '{"value":17}'),
                    ToolContext("test", frozenset(names), AsyncMock()),
                )
            self.assertFalse(result.is_error)
            self.assertEqual(json.loads(result.model_content), {"value": 17})

    async def test_streamable_http_discovery_and_text_result(self):
        import httpx

        def endpoint(request):
            message = json.loads(request.content)
            method = message["method"]
            if method == "initialize":
                result = {
                    "protocolVersion": message["params"]["protocolVersion"],
                    "capabilities": {"tools": {}}, "serverInfo": {"name": "test", "version": "1"},
                }
            elif method == "tools/list":
                result = {"tools": [{
                    "name": "lookup", "description": "Look up data.", "inputSchema": {"type": "object"},
                }]}
            elif method == "tools/call":
                result = {"content": [{"type": "text", "text": "found it"}]}
            else:
                return httpx.Response(202)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

        http_client = httpx.AsyncClient
        servers = read_servers(Path.cwd(), {
            "$schema": MCP_SCHEMA,
            "mcpServers": {"test": {"type": "streamable-http", "url": "https://example.invalid/mcp"}},
        })
        registry = ToolRegistry()
        with patch("httpx.AsyncClient", side_effect=lambda **kwargs: http_client(
            transport=httpx.MockTransport(endpoint), **kwargs,
        )):
            async with AsyncExitStack() as stack:
                names = await register_mcp_tools(stack, registry, servers)
                result = await registry.invoke(
                    ToolCall("mcp-call", names[0], "{}"),
                    ToolContext("test", frozenset(names), AsyncMock()),
                )
            async with AsyncExitStack() as stack:
                with self.assertRaisesRegex(SandboxError, "MCP tools not found: test/missing"):
                    await register_mcp_tools(stack, ToolRegistry(), servers, selected=("test/missing",))
        self.assertFalse(result.is_error)
        self.assertEqual(result.model_content, "found it")
