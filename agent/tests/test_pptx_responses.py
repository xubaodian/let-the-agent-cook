import base64
import io
import json
import os
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent
from unittest.mock import patch

from examples.pptx_validation import CASES, SEEDREAM, run_case
from agent_cook.sandbox.docker import DockerSandboxFactory
from test_openrouter_responses import function_item, reply, text_item

IMAGE = os.environ.get("AGENT_COOK_TEST_DOCKER_IMAGE")
_BUILD = dedent("""
    import json
    import shutil
    import sys
    from pathlib import Path
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt

    out = Path("deliverables")
    out.mkdir()
    cover = out / "cover.png"
    shutil.copyfile(sys.argv[1], cover)
    headings = json.loads(sys.argv[2])
    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.333), Inches(7.5)
    for number, title in enumerate(headings, 1):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        heading = slide.shapes.add_textbox(Inches(0.75), Inches(0.55), Inches(11.8), Inches(0.8))
        heading.text_frame.paragraphs[0].text = title
        heading.text_frame.paragraphs[0].font.size = Pt(36)
        heading.text_frame.paragraphs[0].font.color.rgb = RGBColor.from_string("162426")
        body = slide.shapes.add_textbox(Inches(0.75), Inches(1.65), Inches(10.5), Inches(0.6))
        body.text = "Fixture slide for the PowerPoint delivery regression."
        if number == 1:
            slide.shapes.add_picture(str(cover), Inches(0.75), Inches(2.55), width=Inches(8))
    deck.save(out / "deck.pptx")
    assert len(Presentation(out / "deck.pptx").slides) == len(headings)
""")


@unittest.skipUnless(IMAGE, "Set AGENT_COOK_TEST_DOCKER_IMAGE to validate PPTX export in Docker.")
class PowerPointResponsesTest(unittest.TestCase):
    def test_three_topics_use_the_sdk_docker_and_final_pptx_delivery(self):
        import httpx
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (128, 72), "#1F655E").save(buffer, format="PNG")
        png = base64.b64encode(buffer.getvalue()).decode("ascii")
        with TemporaryDirectory() as directory:
            for case in CASES:
                with self.subTest(topic=case.slug):
                    paths, bodies = [], []
                    diagnostics = io.StringIO()

                    def respond(request):
                        paths.append(request.url.path)
                        body = json.loads(request.content)
                        if request.url.path == "/api/v1/images":
                            bodies.append(body)
                            return httpx.Response(200, json={"data": [{"b64_json": png, "media_type": "image/png"}]})
                        results = [item for item in body["input"] if item.get("type") == "function_call_output"]
                        step = len(results)
                        if step == 0:
                            item = function_item("read_file", "read_skill", path="pptx/SKILL.md")
                        elif step == 1:
                            item = function_item("generate_image", "make_cover", prompt=case.brief, aspect_ratio="16:9")
                        elif step == 2:
                            path = json.loads(results[-1]["output"])["path"]
                            item = function_item(
                                "run_command", "build_deck",
                                argv=["python", "-c", _BUILD, path, json.dumps(case.headings, ensure_ascii=False)],
                            )
                        elif step == 3:
                            item = function_item("submit_results", "submit", paths=[
                                "deliverables/deck.pptx", "deliverables/cover.png",
                            ])
                        else:
                            item = text_item("Verified and delivered the editable deck.")
                        return httpx.Response(200, json=reply(item))

                    root = Path(directory) / case.slug
                    with (
                        patch("agent_cook.runner.AsyncClient", side_effect=lambda **kwargs: httpx.AsyncClient(
                            transport=httpx.MockTransport(respond), **kwargs,
                        )),
                        patch.dict(os.environ, {"OPENROUTER_API_KEY": "fixture-key"}),
                        patch("agent_cook.cli.NativeSandboxFactory", return_value=DockerSandboxFactory(IMAGE)),
                        redirect_stdout(io.StringIO()), redirect_stderr(diagnostics),
                    ):
                        try:
                            delivered = run_case(case, root, model=case.model, image_model=SEEDREAM)
                        except ValueError as error:
                            self.fail(f"{error}\n{diagnostics.getvalue()}")
                    self.assertEqual(paths.count("/api/v1/responses"), 5)
                    self.assertEqual(paths.count("/api/v1/images"), 1)
                    self.assertEqual(bodies, [{"model": SEEDREAM, "prompt": case.brief, "aspect_ratio": "16:9"}])
                    if output := os.environ.get("AGENT_COOK_TEST_PPT_OUTPUT"):
                        copy = Path(output) / case.slug
                        copy.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(delivered / "deck.pptx", copy / "deck.pptx")
