import os
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from agent_cook.sandbox.interface import Sandbox, SandboxError

_DOWNLOAD = "https://www.libreoffice.org/download/"


def office_executable() -> Path | None:
    if sys.platform == "win32":
        roots = (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
        )
        candidates = tuple(root / "LibreOffice/program/soffice.com" for root in roots if root.is_absolute())
    elif sys.platform == "darwin":
        candidates = tuple(
            root / "LibreOffice.app/Contents/MacOS/soffice"
            for root in (Path("/Applications"), Path.home() / "Applications")
        )
    else:
        candidates = (Path("/usr/bin/soffice"), Path("/usr/local/bin/soffice"))
    return next((path.resolve() for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)


def _homebrew() -> str | None:
    brew = shutil.which("brew")
    if brew is not None:
        return brew
    return next(
        (str(path) for path in (Path("/opt/homebrew/bin/brew"), Path("/usr/local/bin/brew"))
         if os.access(path, os.X_OK) and path.is_file()),
        None,
    )


def prepare_office() -> None:
    if office_executable() is not None:
        return
    if sys.platform == "darwin" and (brew := _homebrew()):
        commands = [(brew, "install", "--cask", "libreoffice")]
    elif sys.platform == "win32" and (winget := shutil.which("winget.exe")):
        commands = [(winget, "install", "--id", "TheDocumentFoundation.LibreOffice", "--exact", "--source", "winget")]
    elif sys.platform.startswith("linux") and (apt := shutil.which("apt-get")):
        prefix = () if os.geteuid() == 0 else ("sudo",)
        commands = [
            (*prefix, apt, "update"),
            (*prefix, apt, "install", "-y", "--no-install-recommends", "libreoffice-impress", "fonts-noto-cjk"),
        ]
    else:
        raise SandboxError(f"Install LibreOffice for this platform from {_DOWNLOAD}, then retry.")
    print("Preparing LibreOffice for PowerPoint rendering …", file=sys.stderr, flush=True)
    try:
        for argv in commands:
            subprocess.run(argv, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise SandboxError(f"Could not install LibreOffice. See {_DOWNLOAD} for installers.") from error
    if office_executable() is None:
        raise SandboxError("LibreOffice installation finished, but Agent Cook could not find soffice.")


async def probe_office(sandbox: Sandbox) -> None:
    deck = "预览 样例.pptx"
    pdf = "预览 样例.pdf"
    make_deck = dedent("""
        import sys
        from pptx import Presentation

        deck = Presentation()
        for title in ("Agent Cook", "预览检查"):
            slide = deck.slides.add_slide(deck.slide_layouts[0])
            slide.shapes.title.text = title
        deck.save(sys.argv[1])
    """)
    render_pages = dedent("""
        import sys
        import pypdfium2

        with pypdfium2.PdfDocument(sys.argv[1]) as document:
            if len(document) != 2:
                raise RuntimeError(f"Expected 2 PDF pages, got {len(document)}.")
            for index, page in enumerate(document, 1):
                page.render().to_pil().save(f"doctor-slide-{index}.png")
    """)
    stages = (
        ("create the sample deck", ("python", "-I", "-c", make_deck, deck), 20),
        (
            "export PPTX to PDF",
            ("soffice", "--headless", "--convert-to", "pdf:impress_pdf_Export", "--outdir", ".", deck),
            60,
        ),
        ("render the PDF pages to PNG", ("python", "-I", "-c", render_pages, pdf), 20),
    )
    for step, argv, timeout in stages:
        try:
            result = await sandbox.exec(argv, cwd=".", timeout_seconds=timeout)
        except SandboxError as error:
            raise SandboxError(f"PPTX rendering check could not {step}. {error}") from error
        detail = (result.stderr.strip() or result.stdout.strip())[-450:]
        if result.timed_out or result.exit_code:
            status = f"timed out after {timeout} seconds" if result.timed_out else f"exit code {result.exit_code}"
            raise SandboxError(f"PPTX rendering check could not {step} ({status}). {detail}".rstrip())
        if argv[0] == "soffice":
            try:
                await sandbox.get_file(pdf)
            except SandboxError as error:
                raise SandboxError(f"LibreOffice exited successfully without producing the PDF. {detail}".rstrip()) from error
