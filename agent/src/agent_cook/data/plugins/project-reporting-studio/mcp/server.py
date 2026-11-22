import csv
from datetime import date
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

Dataset = Literal["sales", "category_benchmarks"]
DATA = Path(__file__).resolve().parent / "data"
DATASETS = {
    "sales": {
        "grain": "Order observations. Duplicate order IDs may occur across pages.",
        "date": "Order date (YYYY-MM-DD)",
        "order_id": "Order key used for deduplication",
        "market": "Two-letter market code",
        "category": "Shop category",
        "gmv_cents": "Reported order value in USD cents. Null means unknown.",
    },
    "category_benchmarks": {
        "grain": "One average order value per market, category and week starting Monday.",
        "date": "First day of the benchmark week (YYYY-MM-DD)",
        "market": "Two-letter market code",
        "category": "Shop category",
        "aov_cents": "Category average order value in USD cents.",
    },
}
mcp = FastMCP("fixture-data")


@mcp.tool()
def describe_dataset(dataset: Dataset) -> dict[str, object]:
    """Describe the fields, units and grain of the sales or category_benchmarks fixture."""
    return {"dataset": dataset, **DATASETS[dataset]}


@mcp.tool()
def query_rows(
    dataset: Dataset,
    start_date: str,
    end_date: str,
    market: str | None = None,
    category: str | None = None,
    cursor: int = 0,
    page_size: int = 4,
) -> dict[str, object]:
    """Read a page of fixture rows in the inclusive date range. Pass next_cursor for the next page."""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > end:
        raise ValueError("start_date must be on or before end_date.")
    if cursor < 0 or not 1 <= page_size <= 25:
        raise ValueError("Use a nonnegative cursor and a page_size between 1 and 25.")

    with (DATA / f"{dataset}.csv").open(newline="", encoding="utf-8") as source:
        rows = [
            row for row in csv.DictReader(source)
            if start <= date.fromisoformat(row["date"]) <= end
            and (market is None or row["market"] == market.upper())
            and (category is None or row["category"] == category)
        ]
    amount = "gmv_cents" if dataset == "sales" else "aov_cents"
    next_cursor = cursor + page_size
    return {
        "dataset": dataset,
        "rows": [
            {**row, amount: int(row[amount]) if row[amount] else None}
            for row in rows[cursor:next_cursor]
        ],
        "next_cursor": next_cursor if next_cursor < len(rows) else None,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
