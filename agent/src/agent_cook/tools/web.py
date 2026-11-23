from pathlib import Path
from urllib.parse import urlsplit

from agent_cook.extensions.mcp import McpServer, call_mcp_tool
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.spec import ToolContext, ToolDefinition, ToolError, ToolResult

_EXA_MCP_URL = "https://mcp.exa.ai/mcp?tools=web_search_exa,web_fetch_exa&defaultSearchType=lite"


def register_exa_tools(registry: ToolRegistry, api_key: str) -> tuple[str, str]:
    server = McpServer("exa", Path.cwd(), {
        "type": "streamable-http", "url": _EXA_MCP_URL, "headers": {"x-api-key": api_key},
    })

    async def search(args: dict, _context: ToolContext) -> ToolResult:
        query = args["query"]
        if not query.strip():
            raise ToolError("Provide the complete search query with at least one non-whitespace character.")
        return await call_mcp_tool(server, "web_search_exa", {
            "query": query, "numResults": args.get("limit", 5),
        })

    async def fetch(args: dict, _context: ToolContext) -> ToolResult:
        url = args["url"]
        error_message = "Provide the full public HTTP or HTTPS URL, including its hostname."
        try:
            target = urlsplit(url)
            port = target.port
        except ValueError as error:
            raise ToolError(error_message) from error
        if (
            target.scheme not in ("http", "https") or not target.hostname
            or target.username is not None or port == 0 or "\\" in url
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url)
        ):
            raise ToolError(error_message)
        return await call_mcp_tool(server, "web_fetch_exa", {
            "urls": [url], "maxCharacters": 4000,
        })

    # Some constrained tool decoders generate just the matching substring of a pattern.
    # Keep model-facing strings unconstrained by regex and validate their contents above.
    registry.register(ToolDefinition(
        "web_search", "Search the public web for sources. Returns titles, URLs, and relevant excerpts.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "description": "The full search query, including all words."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Default: 5."},
            },
            "required": ["query"], "additionalProperties": False,
        },
    ), search)
    registry.register(ToolDefinition(
        "web_fetch", "Read a known public URL and return cleaned page text.",
        {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string", "minLength": 1,
                    "description": "The complete HTTP or HTTPS URL, including the hostname, path, and query when present.",
                },
            },
            "required": ["url"], "additionalProperties": False,
        },
    ), fetch)
    return "web_search", "web_fetch"
