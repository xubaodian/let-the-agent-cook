import argparse
import json
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from pptx import Presentation
from pptx.util import Inches, Pt

CATEGORY_LABELS = {"home-living": "Rumah dan Hunian", "fashion": "Mode"}
USD_FORMAT = '"US$"#,##0.00'


def calculate(sales: list[dict], benchmarks: list[dict], market: str, start: date) -> tuple[dict, list[dict]]:
    end = start + timedelta(days=6)
    unique = {}
    duplicates = 0
    for row in sales:
        if row["market"] != market or not start <= date.fromisoformat(row["date"]) <= end:
            continue
        order_id = row["order_id"]
        if order_id in unique:
            if row != unique[order_id]:
                raise ValueError(f"Conflicting rows for order {order_id}.")
            duplicates += 1
        else:
            unique[order_id] = row

    benchmark_by_category = {
        row["category"]: row["aov_cents"] for row in benchmarks
        if row["market"] == market and row["date"] == start.isoformat()
    }
    orders = list(unique.values())
    groups: dict[str, list[dict]] = {}
    for row in orders:
        groups.setdefault(row["category"], []).append(row)
    categories = []
    for category, rows in sorted(groups.items()):
        amounts = [row["gmv_cents"] for row in rows if row["gmv_cents"] is not None]
        categories.append({
            "category": category,
            "label": CATEGORY_LABELS.get(category, category),
            "orders": len(rows),
            "valued_orders": len(amounts),
            "gmv_cents": sum(amounts) if amounts else None,
            "aov_cents": int((Decimal(sum(amounts)) / len(amounts)).quantize(Decimal("1"), ROUND_HALF_UP))
            if amounts else None,
            "benchmark_cents": benchmark_by_category.get(category),
        })
    total = [row["gmv_cents"] for row in orders if row["gmv_cents"] is not None]
    metrics = {
        "market": market,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "currency": "USD",
        "orders": len(orders),
        "valued_orders": len(total),
        "duplicate_rows": duplicates,
        "gmv_cents": sum(total) if total else None,
        "categories": categories,
    }
    return metrics, orders


def format_usd_indonesian(cents: int | None) -> str:
    if cents is None:
        return "Belum tersedia"
    formatted = f"{Decimal(cents) / 100:,.2f}".translate(str.maketrans(",.", ".,"))
    return f"US${formatted}"


def _usd(cents: int | None) -> float | None:
    return cents / 100 if cents is not None else None


def _period(metrics: dict) -> str:
    return f'{metrics["market"]} | {metrics["start_date"]} – {metrics["end_date"]}'


def _coverage(metrics: dict) -> str:
    return f'{metrics["valued_orders"]} dari {metrics["orders"]} pesanan memiliki nilai.'


def _category_line(category: dict) -> str:
    return (
        f'{category["label"]} · {format_usd_indonesian(category["aov_cents"])} · '
        f'Benchmark industri kategori {category["label"]} {format_usd_indonesian(category["benchmark_cents"])}'
    )


def write_workbook(path: Path, metrics: dict, raw: list[dict], orders: list[dict]) -> None:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Metrics"
    summary.append(["Metric", "Verified value", "Recalculation from Orders"])
    summary.append(["Orders", metrics["orders"], "=COUNTA(Orders!B:B)-1"])
    summary.append(["Orders with known amount", metrics["valued_orders"], "=COUNT(Orders!E:E)"])
    summary.append(["Reported GMV (USD)", _usd(metrics["gmv_cents"]), '=IF(C3=0,"",SUM(Orders!E:E))'])
    summary.append(["Duplicate rows removed", metrics["duplicate_rows"]])
    summary.append(["Market and period", _period(metrics)])
    summary["B4"].number_format = USD_FORMAT
    summary["C4"].number_format = USD_FORMAT
    for name, rows in (("Raw", raw), ("Orders", orders)):
        sheet = workbook.create_sheet(name)
        sheet.append(["Date", "Order ID", "Market", "Category", "Reported value (USD)"])
        for row in rows:
            sheet.append([row["date"], row["order_id"], row["market"], row["category"], _usd(row["gmv_cents"])])
            sheet.cell(sheet.max_row, 5).number_format = USD_FORMAT
        sheet.auto_filter.ref = sheet.dimensions

    by_category = workbook.create_sheet("Categories")
    by_category.append(["Category", "Orders", "Known amounts", "GMV", "AOV", "Category benchmark"])
    for item in metrics["categories"]:
        by_category.append([
            item["label"], item["orders"], item["valued_orders"], _usd(item["gmv_cents"]),
            _usd(item["aov_cents"]), _usd(item["benchmark_cents"]),
        ])
        for cell in by_category[by_category.max_row][3:]:
            cell.number_format = USD_FORMAT

    checks = workbook.create_sheet("Checks")
    checks.append(["Check", "Result"])
    checks.append(["Source", "fixture-data/sales and fixture-data/category_benchmarks"])
    checks.append(["Missing amounts", metrics["orders"] - metrics["valued_orders"]])
    checks.append(["Category order counts", sum(item["orders"] for item in metrics["categories"])])
    for sheet in workbook:
        sheet.freeze_panes = "A2"
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for column in sheet.columns:
            width = max(len(str(cell.value or "")) for cell in column)
            sheet.column_dimensions[column[0].column_letter].width = max(15, min(64, width + 3))
    workbook.save(path)


def write_deck(path: Path, metrics: dict) -> None:
    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.333), Inches(7.5)
    slides = (
        ("Tinjauan kinerja mingguan", [
            _period(metrics), f'GMV tercatat {format_usd_indonesian(metrics["gmv_cents"])}', _coverage(metrics),
        ]),
        ("Nilai pesanan per kategori", [_category_line(item) for item in metrics["categories"]]),
        ("Pemeriksaan berikutnya", [
            "Lengkapi nilai pesanan yang belum tersedia.",
            "Gunakan benchmark sesuai kategori, pasar, dan periode.",
        ]),
    )
    for title, lines in slides:
        slide = deck.slides.add_slide(deck.slide_layouts[5])
        slide.shapes.title.text = title
        box = slide.shapes.add_textbox(Inches(0.8), Inches(2), Inches(11.6), Inches(4.6))
        box.text_frame.word_wrap = True
        box.text_frame.text = "\n".join(lines)
        for paragraph in box.text_frame.paragraphs:
            paragraph.font.size = Pt(25)
            paragraph.space_after = Pt(18)
    deck.save(path)


def build_report(output: Path, metrics: dict, raw: list[dict], orders: list[dict], deck_only: bool) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    metrics_path, deck_path = output / "metrics.json", output / "review.pptx"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    write_deck(deck_path, metrics)
    files = [metrics_path, deck_path]
    deck = Presentation(deck_path)
    total = format_usd_indonesian(metrics["gmv_cents"])
    checks = {
        "order_counts_reconcile": sum(item["orders"] for item in metrics["categories"]) == metrics["orders"],
        "deck_matches_metrics": any(total in shape.text for shape in deck.slides[0].shapes if shape.has_text_frame),
    }
    if not deck_only:
        book, brief = output / "analysis.xlsx", output / "brief.md"
        write_workbook(book, metrics, raw, orders)
        brief.write_text("\n".join([
            "# Tinjauan kinerja mingguan", "", _period(metrics), "",
            f"GMV tercatat **{total}**. {_coverage(metrics)}", "",
            *[f"- {_category_line(item)}" for item in metrics["categories"]],
            "", "Lengkapi nilai pesanan yang belum tersedia sebelum menilai keseluruhan periode.", "",
        ]), encoding="utf-8")
        with book.open("rb") as stream:
            workbook = load_workbook(stream, read_only=True, data_only=False)
            try:
                summary = workbook["Metrics"]
                checks["workbook_matches_metrics"] = (
                    summary["B2"].value == metrics["orders"]
                    and summary["B4"].value == _usd(metrics["gmv_cents"])
                    and summary["C4"].data_type == "f"
                )
            finally:
                workbook.close()
        checks["brief_matches_metrics"] = total in brief.read_text(encoding="utf-8")
        files.extend((book, brief))
    (output / "validation.json").write_text(json.dumps({
        "files": [path.as_posix() for path in files],
        "checks": checks,
    }, indent=2), encoding="utf-8")
    if not all(checks.values()):
        raise ValueError("A report check failed. Inspect validation.json.")
    return files


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build a fixture weekly review from JSON arrays of MCP rows.")
    parser.add_argument("--sales", type=Path, required=True)
    parser.add_argument("--benchmarks", type=Path, required=True)
    parser.add_argument("--market", default="ID")
    parser.add_argument("--week-start", type=date.fromisoformat, default=date(2026, 10, 5))
    parser.add_argument("--deck-only", action="store_true")
    args = parser.parse_args()
    sales = json.loads(args.sales.read_text(encoding="utf-8"))
    benchmarks = json.loads(args.benchmarks.read_text(encoding="utf-8"))
    metrics, orders = calculate(sales, benchmarks, args.market, args.week_start)
    print(json.dumps({
        "files": [path.as_posix() for path in build_report(Path("out/report"), metrics, sales, orders, args.deck_only)],
        "metrics": "out/report/metrics.json",
    }))
