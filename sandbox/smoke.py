import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from docx import Document
from openpyxl import load_workbook
from PIL import Image
from pptx import Presentation
from pypdf import PdfReader, PdfWriter
from scipy.linalg import det

from agent_cook.sandbox.worker.browser import playwright_preview
from agent_cook.sandbox.worker.workspace import Workspace


def check_files(directory: Path) -> None:
    assert det(np.eye(2)) == 1
    xlsx = directory / "sample.xlsx"
    pd.DataFrame({"value": [2, 4]}).to_excel(xlsx, engine="xlsxwriter", index=False)
    workbook = load_workbook(xlsx)
    assert workbook.active["A3"].value == 4
    workbook.close()

    pptx = directory / "sample.pptx"
    presentation = Presentation()
    presentation.slides.add_slide(presentation.slide_layouts[6])
    presentation.save(pptx)
    assert len(Presentation(pptx).slides) == 1

    docx = directory / "sample.docx"
    document = Document()
    document.add_paragraph("Agent Cook")
    document.save(docx)
    assert Document(docx).paragraphs[0].text == "Agent Cook"

    pdf = directory / "sample.pdf"
    pages = PdfWriter()
    pages.add_blank_page(width=120, height=120)
    pages.write(pdf)
    assert len(PdfReader(pdf).pages) == 1

    plot = directory / "plot.png"
    plt.plot([2, 4])
    plt.savefig(plot)
    plt.close()
    with Image.open(plot) as image:
        assert image.format == "PNG"


def main() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        check_files(root)
        subprocess.run(["node", "-e", "if (2 + 4 !== 6) process.exit(1)"], check=True)
        subprocess.run(["npm", "--version"], check=True)
        (root / "index.html").write_text("<h1>Agent Cook</h1>", encoding="utf-8")
        result = playwright_preview(
            Workspace(root), path="index.html", screenshot="proof.png",
            actions=[{"action": "expect_text", "selector": "h1", "value": "Agent Cook"}],
        )
        assert not result["page_errors"]
        with Image.open(root / "proof.png") as image:
            assert image.format == "PNG"


if __name__ == "__main__":
    main()
