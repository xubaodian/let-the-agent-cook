import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent_cook.sandbox.interface import SandboxError

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
    return next((path.resolve() for path in candidates if path.is_file()), None)


def prepare_office() -> None:
    if office_executable() is not None:
        return
    if sys.platform == "darwin" and (brew := shutil.which("brew")):
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
    try:
        for argv in commands:
            subprocess.run(argv, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise SandboxError(f"Could not install LibreOffice. See {_DOWNLOAD} for installers.") from error
    if office_executable() is None:
        raise SandboxError("LibreOffice installation finished, but Agent Cook could not find soffice.")
