import os
import stat
from pathlib import PurePosixPath

_DEVICES = {
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    *(f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "123456789¹²³"),
}


def workspace_path(value: str, *, allow_root: bool = False) -> str:
    if (
        not value
        or len(value.encode("utf-8")) > 1024
        or value.startswith("/")
        or "\\" in value
        or any(ord(char) < 32 for char in value)
        or ".." in value.split("/")
    ):
        raise ValueError("Use a relative path inside the task workspace.")
    path = PurePosixPath(value).as_posix()
    if path == "." and not allow_root:
        raise ValueError("Provide a file path inside the task workspace.")
    return path


def portable_path(value: str, *, allow_root: bool = False) -> str:
    path = workspace_path(value, allow_root=allow_root)
    for part in PurePosixPath(path).parts:
        if (
            part.endswith((" ", "."))
            or part.split(".", 1)[0].rstrip(" ").upper() in _DEVICES
            or any(char in '<>:"|?*' for char in part)
            or len(part.encode("utf-8")) > 255
        ):
            raise ValueError(f"Path '{value}' cannot be used on Windows, macOS and Linux.")
    return path


def is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )
