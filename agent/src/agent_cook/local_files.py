import os
import stat
import unicodedata
from collections import deque
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from agent_cook.sandbox.limits import MAX_FILE_BYTES, MAX_SCAN_ENTRIES, MAX_TRANSFER_BYTES
from agent_cook.sandbox.paths import workspace_path

_DEVICES = {
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    *(f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "123456789¹²³"),
}


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


class PortablePaths:
    """Detect normalized spelling collisions, including in parent directories."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, str]] = {}

    def add(self, path: str, kind: str = "file") -> None:
        relative = PurePosixPath(path)
        for part in (*reversed(relative.parents), relative):
            name = part.as_posix()
            if name == ".":
                continue
            key = unicodedata.normalize("NFC", name.casefold())
            entry = name, kind if part == relative else "directory"
            previous = self._entries.setdefault(key, entry)
            if previous != entry:
                raise ValueError(f"Workspace path '{path}' conflicts with '{previous[0]}'.")


def _link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


class LocalTree:
    """Read host files selected for copying into a workspace."""

    def __init__(self, root: Path) -> None:
        info = root.lstat()
        if _link(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Directory '{root}' must be a regular directory.")
        self.root = root.resolve(strict=True)

    def _entry(self, path: str) -> tuple[Path, os.stat_result]:
        target = self.root
        info = target.lstat()
        for part in PurePosixPath(path).parts:
            if _link(info) or not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"'{target}' must be a regular directory.")
            target /= part
            info = target.lstat()
        if _link(info) or not target.resolve(strict=True).is_relative_to(self.root):
            raise ValueError(f"'{target}' must stay within '{self.root}' and must not be a link.")
        return target, info

    @contextmanager
    def open_file(self, path: str):
        target, info = self._entry(portable_path(path))
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"'{target}' must be a regular file.")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(target, flags), "rb") as stream:
            actual = os.fstat(stream.fileno())
            if not stat.S_ISREG(actual.st_mode) or not os.path.samestat(info, actual):
                raise ValueError(f"'{target}' changed while being read.")
            yield stream

    def read_bytes(self, path: str, limit: int = MAX_FILE_BYTES) -> bytes:
        with self.open_file(path) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"'{self.root / path}' exceeds the {limit}-byte transfer limit for this file.")
        return data

    def entries(self, directory: str, *, recursive: bool, scan_limit: int = MAX_SCAN_ENTRIES):
        pending = deque([portable_path(directory, allow_root=True)])
        found: list[tuple[str, str]] = []
        count = 0
        while pending:
            parent = pending.popleft()
            folder, info = self._entry(parent)
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"'{folder}' must be a directory.")
            with os.scandir(folder) as entries:
                for entry in entries:
                    count += 1
                    if count > scan_limit:
                        return sorted(found), True
                    info = entry.stat(follow_symlinks=False)
                    if _link(info):
                        continue
                    relative = (PurePosixPath(parent) / entry.name).as_posix()
                    if stat.S_ISDIR(info.st_mode):
                        found.append((relative, "directory"))
                        if recursive:
                            pending.append(portable_path(relative))
                    elif stat.S_ISREG(info.st_mode):
                        found.append((portable_path(relative), "file"))
        return sorted(found), False


def collect_inputs(paths: list[Path]) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    roots: dict[str, Path] = {}
    index = PortablePaths()
    remaining = MAX_TRANSFER_BYTES
    for path in paths:
        path = path.absolute()
        info = path.lstat()
        if _link(info) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ValueError(f"Input '{path}' must be a regular file or directory.")
        try:
            name = portable_path(path.name)
            if name in roots:
                raise ValueError(f"It conflicts with '{roots[name]}'.")
            if stat.S_ISDIR(info.st_mode):
                index.add(name, "directory")
                source = LocalTree(path)
                entries, limited = source.entries(".", recursive=True)
                if limited:
                    raise ValueError(f"The folder exceeds the {MAX_SCAN_ENTRIES}-entry scan limit.")
                prefix = name + "/"
            else:
                source = LocalTree(path.parent.resolve(strict=True))
                entries = [(name, "file")]
                prefix = ""
            roots[name] = path
            for child, kind in entries:
                relative = portable_path(prefix + child)
                index.add(relative, kind)
                if kind == "file":
                    data = source.read_bytes(child, min(MAX_FILE_BYTES, remaining))
                    files[relative] = data
                    remaining -= len(data)
        except ValueError as error:
            raise ValueError(f"Input '{path}': {error}") from error
    return files
