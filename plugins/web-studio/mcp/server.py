import argparse
import json
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DOCUMENTS = json.loads(Path(__file__).with_name("documents.json").read_text(encoding="utf-8"))


def _terms(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


def create_server(port: int) -> FastMCP:
    mcp = FastMCP("reference-library", host="127.0.0.1", port=port, stateless_http=True, json_response=True)

    @mcp.tool()
    def search(
        query: str, locale: str | None = None, include_archived: bool = False, limit: int = 5,
    ) -> dict[str, object]:
        """Search fixture documents and return matching excerpts with source IDs and versions."""
        terms = _terms(query)
        if not terms or not 1 <= limit <= 8:
            raise ValueError("Supply a search term and a limit between 1 and 8.")

        matches = []
        for document in DOCUMENTS:
            if locale is not None and document["locale"].casefold() != locale.casefold():
                continue
            if not include_archived and not document["current"]:
                continue
            score = len(terms & _terms(f'{document["title"]} {document["excerpt"]}'))
            if score:
                matches.append((score, document))
        matches.sort(key=lambda item: (-item[0], item[1]["id"], -item[1]["version"]))
        return {"hits": [document for _, document in matches[:limit]]}

    return mcp


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serve the example reference library over MCP.")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    create_server(args.port).run(transport="streamable-http")
