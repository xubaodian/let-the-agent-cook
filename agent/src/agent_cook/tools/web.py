from pathlib import Path

from agent_cook.extensions.mcp import McpServer, call_mcp_tool
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.spec import ToolContext, ToolDefinition, ToolResult

_EXA_MCP_URL = "https://mcp.exa.ai/mcp?tools=web_search_exa,web_fetch_exa&defaultSearchType=lite"


def register_exa_tools(registry: ToolRegistry, api_key: str) -> tuple[str, str]:
    server = McpServer("exa", Path.cwd(), {
        "type": "streamable-http", "url": _EXA_MCP_URL, "headers": {"x-api-key": api_key},
    })

    async def search(args: dict, _context: ToolContext) -> ToolResult:
        return await call_mcp_tool(server, "web_search_exa", {
            "query": args["query"], "numResults": args.get("limit", 5),
        })

    async def fetch(args: dict, _context: ToolContext) -> ToolResult:
        return await call_mcp_tool(server, "web_fetch_exa", {
            "urls": [args["url"]], "maxCharacters": 4000,
        })

    registry.register(ToolDefinition(
        "web_search", "Search the public web for sources. Returns titles, URLs, and relevant excerpts.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "pattern": r"\S"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Default: 5."},
            },
            "required": ["query"], "additionalProperties": False,
        },
    ), search)
    registry.register(ToolDefinition(
        "web_fetch", "Read a known public URL and return cleaned page text.",
        {
            "type": "object", "properties": {"url": {"type": "string", "pattern": "^https?://"}},
            "required": ["url"], "additionalProperties": False,
        },
    ), fetch)
    return "web_search", "web_fetch"
