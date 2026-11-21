"""Run the PowerPoint skill against multiple topics and inspect the delivered decks."""

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from agent_cook.cli import main as agent_cook

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "plugins/reporting-studio/skills"
DEEPSEEK = "deepseek/deepseek-v4.1-flash-1017"
GROK = "x-ai/grok-4.7"
SEEDREAM = "bytedance-seed/seedream-5-5"


@dataclass(frozen=True, slots=True)
class Case:
    slug: str
    model: str
    headings: tuple[str, ...]
    brief: str


CASES = (
    Case(
        "retail-review", DEEPSEEK, ("门店经营复盘", "销量与收入", "渠道表现", "下月行动"),
        "用简体中文给零售团队汇报 10 月经营。数据为虚构样例。销量 1,200 件，收入 96,000 元，线上"
        "收入 57,600 元，线下 38,400 元。用可编辑图表或表格呈现线上线下。列出两项下一步行动。",
    ),
    Case(
        "museum-exhibition", GROK, ("海风与港口", "展览主线", "三件展品", "参观路线", "开幕准备"),
        "为一场虚构的城市博物馆展览写简体中文内部提案。展品包括港区旧地图、修船工的工具箱、"
        "口述历史录音。用编辑可修改的文字讲清故事、动线和筹备任务。配图是主题插画，不冒充史料。",
    ),
    Case(
        "agent-workspace", DEEPSEEK,
        ("Agent Cook", "Select inputs", "Work in isolation", "Deliver results"),
        "Write an English onboarding deck. Explain @ and --input for files and folders, the copied workspace "
        "in a native OS sandbox, and submit_results selecting the final result directory. Use a diagram with editable "
        "labels for the workflow. Audience: colleagues trying the CLI for the first time.",
    ),
)


def task_for(case: Case) -> str:
    headings = "\n".join(f"{i}. {title}" for i, title in enumerate(case.headings, 1))
    return (
        f"{case.brief}\n\nBuild exactly {len(case.headings)} editable PowerPoint slides with these exact "
        f"titles in order:\n{headings}\n\nUse the pptx skill and python-pptx. Use generate_image to create "
        "one relevant 16:9 title-slide visual. Use its workspace path. If needed, use Pillow to convert it "
        "to JPEG or PNG. Place the exact JPEG or PNG that you embed on the first slide at "
        "deliverables/cover.jpg or deliverables/cover.png. Put each title in its own editable text box "
        "and include editable body text on every slide. "
        "Save the editable deck as deliverables/deck.pptx. Reopen it to verify the slide count and order. "
        "Use submit_results with exactly the deck and the embedded cover image."
    )


def run_case(case: Case, root: Path, *, model: str, image_model: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    trace = root / "trace.jsonl"
    status = agent_cook([
        "exec", "-C", str(root),
        "--model", model, "--model-api", "responses", "--image-model", image_model,
        "--skills-dir", str(SKILLS), "--skill", "pptx", "--trace", str(trace),
        "--timeout", "240", "--max-turns", "14", task_for(case),
    ])
    if status:
        raise ValueError(f"{case.slug}: Agent Cook exited with status {status}.")
    result, = root.glob("*-results")
    validate_deck(case, result, trace)
    return result / "deliverables"


def validate_deck(case: Case, result: Path, trace: Path) -> None:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    files = result / "deliverables"
    deck = files / "deck.pptx"
    covers = sorted(files.glob("cover.*"))
    if len(covers) != 1 or covers[0].suffix not in (".jpg", ".png"):
        raise ValueError(f"{case.slug}: expected the JPG or PNG cover used in the deck.")
    cover = covers[0].read_bytes()
    with ZipFile(deck) as archive:
        if damaged := archive.testzip():
            raise ValueError(f"{case.slug}: damaged PowerPoint part {damaged}.")
    slides = Presentation(deck)
    if len(slides.slides) != len(case.headings):
        raise ValueError(f"{case.slug}: expected {len(case.headings)} slides, got {len(slides.slides)}.")
    for index, (slide, expected) in enumerate(zip(slides.slides, case.headings, strict=True), 1):
        texts = [shape.text.strip() for shape in slide.shapes if shape.has_text_frame and shape.text.strip()]
        if expected not in texts or not any(text != expected for text in texts):
            raise ValueError(f"{case.slug}: slide {index} needs the title '{expected}' and editable body text.")
    if not any(
        shape.shape_type == MSO_SHAPE_TYPE.PICTURE and shape.image.blob == cover
        for shape in slides.slides[0].shapes
    ):
        raise ValueError(f"{case.slug}: the submitted cover is missing from slide 1.")

    events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    finished = {event["data"]["name"] for event in events
                if event["type"] == "tool.finished" and not event["data"]["is_error"]}
    if events[-1]["type"] != "run.completed" or not {"generate_image", "submit_results"} <= finished:
        raise ValueError(f"{case.slug}: expected successful image generation and final submission in the run trace.")
    if not any(event["type"] == "model.finished" and event["data"].get("prompt_tokens") for event in events):
        raise ValueError(f"{case.slug}: missing model input token usage.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Directory for verified decks and traces.")
    parser.add_argument("--image-model", default=SEEDREAM)
    parser.add_argument("--model", help="Use this chat model for all topics.")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error("Choose a new or empty --output directory.")
    work = output / "runs"
    for case in CASES:
        model = args.model or case.model
        delivered = run_case(case, work / case.slug, model=model, image_model=args.image_model)
        target = output / case.slug
        target.mkdir()
        shutil.copyfile(delivered / "deck.pptx", target / "deck.pptx")
        shutil.copyfile(work / case.slug / "trace.jsonl", target / "trace.jsonl")
        print(f"PASS {case.slug} | {len(case.headings)} slides | {model} | {args.image_model}", flush=True)


if __name__ == "__main__":
    main()
