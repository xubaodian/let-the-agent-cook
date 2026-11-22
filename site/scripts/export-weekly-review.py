"""Publish the synthetic combined-review example from a checked Agent Cook run."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from pptx import Presentation

SITE = Path(__file__).resolve().parents[1]
REPLAY = "weekly-review"
PUBLIC_PATH = f"replays/{REPLAY}"


def read_events(source: Path) -> list[dict]:
    return [json.loads(line) for line in (source / "trace.jsonl").read_text(encoding="utf-8").splitlines()]


def build_steps(events: list[dict], metrics: dict) -> list[dict]:
    by_type = {event["type"]: event for event in events}
    successful = {
        event["data"]["tool_call_id"]: event
        for event in events
        if event["type"] == "tool.finished" and not event["data"]["is_error"]
    }
    if events[-1]["type"] != "run.completed":
        raise ValueError("The capture must come from a completed run.")
    required = {"load-skill", "sales", "benchmarks", "method", "page-two", "build",
                "dashboard", "same-metrics", "method-file", "dashboard-preview", "submit"}
    if missing := required.difference(successful):
        raise ValueError(f"Missing successful calls in the capture: {sorted(missing)}")
    passed_hooks = {
        event["data"]["plugin"] for event in events
        if event["type"] == "hook.finished" and event["data"]["tool_call_id"] == "submit"
        and not event["data"]["is_error"]
    }
    if passed_hooks != {"reporting-studio", "project-reporting-studio"}:
        raise ValueError("Both Studio submission hooks must pass.")

    gmv = "US$" + f"{metrics['gmv_cents'] / 100:.2f}".replace(".", ",")
    fashion = next(group for group in metrics["categories"] if group["category"] == "fashion")
    subtotal = "US$" + f"{fashion['gmv_cents'] / 100:.2f}".replace(".", ",")
    steps = [
        ("setup", by_type["run.started"], "载入任务",
         "启用两个 Studio，挂载任务所需的技能和两个 MCP 工具。",
         ["extensions.loaded", "run.started"]),
        ("read", successful["page-two"], "读取订单与口径",
         "读数据分析 Skill，分页获取合成订单和类目基准，并查询印尼语 GMV 口径。",
         ["read_file", "fixture-data/query_rows", "reference-library/search"]),
        ("build", successful["build"], "计算指标并生成 PPTX",
         f"脚本保存输入，去重后留下 {metrics['orders']} 个订单。"
         f"其中 {metrics['valued_orders']} 个含金额，GMV 合计 {gmv}。脚本用同一份指标生成可编辑周报。",
         ["write_file", "run_command"]),
        ("dashboard", successful["method-file"], "组装筛选网页",
         "把报告的指标 JSON 复制到网页目录，和筛选界面、资料引用一起保存。",
         ["copy_file", "write_file"]),
        ("checkpoint", by_type["context.compacted"], "压缩并接续",
         "运行时压缩早先的上下文，检查点保留工作区文件路径和剩余的浏览器验收步骤。",
         ["context.compacted"]),
        ("verify", successful["dashboard-preview"], "检查浏览器筛选",
         f"Chromium 检查页面总额 {gmv}，再点击 Mode，验证金额切换到 {subtotal}。",
         ["playwright", "hook.finished"]),
        ("submit", by_type["run.completed"], "提交报告和网页",
         "两个 Studio 的提交 Hook 均通过，PPTX 和站点文件进入同一批交付。",
         ["submit_results", "artifact.created", "run.completed"]),
    ]
    if any(previous[1]["seq"] >= current[1]["seq"] for previous, current in zip(steps, steps[1:])):
        raise ValueError("Unexpected order of replay milestones.")
    return [
        {"id": id_, "seq": event["seq"], "title": title, "detail": detail, "events": labels}
        for id_, event, title, detail, labels in steps
    ]


def offline_dashboard(source: Path, metrics: dict, reference: dict) -> str:
    html = source.read_text(encoding="utf-8")
    fetch_start = html.index("    async function readJSON(path) {")
    fetch_end = html.index("    async function showReview() {")
    data = json.dumps([metrics, reference], ensure_ascii=True).replace("<", r"\u003c")
    html = html[:fetch_start] + f"    const dashboardData = {data};\n\n" + html[fetch_end:]
    lookup = 'await Promise.all([readJSON("metrics.json"), readJSON("reference.json")])'
    if html.count(lookup) != 1:
        raise ValueError("The fixture dashboard's data loading has changed.")
    return html.replace(lookup, "dashboardData")


def publish(source: Path, soffice: str) -> None:
    events = read_events(source)
    report = source / "out/report"
    dashboard = source / "out/site"
    metrics_path = dashboard / "metrics.json"
    if metrics_path.read_bytes() != (report / "metrics.json").read_bytes():
        raise ValueError("The published deck and dashboard must share the same metrics.")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    reference = json.loads((dashboard / "reference.json").read_text(encoding="utf-8"))
    replay = build_steps(events, metrics)

    deck = report / "review.pptx"
    titles = [
        next(shape.text.splitlines()[0] for shape in slide.shapes if shape.has_text_frame and shape.text.strip())
        for slide in Presentation(deck).slides
    ]
    html = offline_dashboard(dashboard / "index.html", metrics, reference)
    output = SITE / "public" / PUBLIC_PATH
    with TemporaryDirectory(prefix="agent-cook-site-") as temp:
        workspace = Path(temp)
        profile = (workspace / "office-profile").as_uri()
        subprocess.run([soffice, f"-env:UserInstallation={profile}", "--headless",
                        "--convert-to", "pdf", "--outdir", temp, str(deck)], check=True)
        subprocess.run(["pdftoppm", "-png", "-scale-to", "1040", str(workspace / "review.pdf"),
                        str(workspace / "slide")], check=True)
        images = sorted(workspace.glob("slide-*.png"), key=lambda path: int(path.stem.split("-")[-1]))
        if len(images) != len(titles):
            raise ValueError("Slide rendering did not match the editable deck.")
        output.mkdir(parents=True, exist_ok=True)
        for stale in output.glob("slide-*.png"):
            stale.unlink()
        slides = []
        for index, (image, title) in enumerate(zip(images, titles, strict=True), start=1):
            filename = f"slide-{index}.png"
            shutil.copyfile(image, output / filename)
            slides.append({"path": f"{PUBLIC_PATH}/{filename}", "title": title})

    shutil.copyfile(deck, output / "review.pptx")
    shutil.copyfile(metrics_path, output / "metrics.json")
    shutil.copyfile(dashboard / "preview.png", output / "dashboard-preview.png")
    (output / "dashboard.html").write_text(html, encoding="utf-8")
    document = {
        "version": 1,
        "steps": replay,
        "previews": [
            {"id": "deck", "kind": "slides", "label": "PowerPoint", "slides": slides},
            {"id": "dashboard", "kind": "html", "label": "筛选网页",
             "path": f"{PUBLIC_PATH}/dashboard.html", "title": "店铺周报筛选网页"},
        ],
        "downloads": [
            {"path": f"{PUBLIC_PATH}/review.pptx", "label": "下载 PPTX"},
            {"path": f"{PUBLIC_PATH}/metrics.json", "label": "指标 JSON"},
        ],
    }
    index_file = SITE / "src/data/replays" / f"{REPLAY}.json"
    index_file.parent.mkdir(parents=True, exist_ok=True)
    index_file.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="Directory captured by the combined-review Chromium test.")
    parser.add_argument("--soffice", default="soffice")
    args = parser.parse_args()
    publish(args.capture.resolve(), args.soffice)
