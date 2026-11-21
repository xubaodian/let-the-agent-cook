import base64
import os
import stat
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from uuid import uuid4

from agent_cook.sandbox.limits import (
    MAX_FILE_BYTES,
    MAX_FIND_RESULTS,
    MAX_READ_CHARS,
    MAX_SCAN_ENTRIES,
    MAX_TEXT_FILE_BYTES,
    MAX_WRITE_BYTES,
)
from agent_cook.sandbox.paths import is_link, portable_path, workspace_path

_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_SEARCH_SKIPS = frozenset({".git", ".venv", "__pycache__", "node_modules"})


def _regular(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and not is_link(info) and info.st_nlink == 1


class PosixWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)

    @contextmanager
    def directory(self, path: str = ".", *, create: bool = False) -> Iterator[int]:
        relative = portable_path(path, allow_root=True)
        directory = os.open(self.root, _DIR_FLAGS)
        try:
            for part in PurePosixPath(relative).parts:
                if create:
                    try:
                        os.mkdir(part, dir_fd=directory)
                    except FileExistsError:
                        pass
                child = os.open(part, _DIR_FLAGS, dir_fd=directory)
                os.close(directory)
                directory = child
            yield directory
        finally:
            os.close(directory)

    def directory_path(self, path: str) -> Path:
        relative = portable_path(path, allow_root=True)
        with self.directory(relative):
            return self.root / relative

    @contextmanager
    def open_file(self, path: str) -> Iterator[BinaryIO]:
        relative = PurePosixPath(portable_path(path))
        with self.directory(relative.parent.as_posix()) as directory:
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            descriptor = os.open(relative.name, flags, dir_fd=directory)
            with os.fdopen(descriptor, "rb") as file:
                if not _regular(os.fstat(file.fileno())):
                    raise ValueError("The path must point to a regular workspace file.")
                yield file

    def read_bytes(self, path: str, limit: int = MAX_FILE_BYTES) -> bytes:
        with self.open_file(path) as file:
            data = file.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"The file exceeds the {limit}-byte limit for this operation.")
        return data

    def file_size(self, path: str) -> int:
        relative = PurePosixPath(portable_path(path))
        with self.directory(relative.parent.as_posix()) as directory:
            info = os.stat(relative.name, dir_fd=directory, follow_symlinks=False)
        if not _regular(info):
            raise ValueError("The path must point to a regular workspace file.")
        return info.st_size

    def write_bytes(self, path: str, data: bytes, *, overwrite: bool) -> None:
        relative = PurePosixPath(portable_path(path))
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("The file exceeds the workspace file size limit.")
        with self.directory(relative.parent.as_posix(), create=True) as directory:
            if overwrite:
                try:
                    info = os.stat(relative.name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if not _regular(info):
                        raise ValueError("The destination must be a regular workspace file.")
            temporary = f".cook-write-{uuid4().hex}"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            descriptor = os.open(temporary, flags, 0o600, dir_fd=directory)
            try:
                with os.fdopen(descriptor, "wb") as file:
                    file.write(data)
                if overwrite:
                    os.replace(temporary, relative.name, src_dir_fd=directory, dst_dir_fd=directory)
                else:
                    os.link(
                        temporary,
                        relative.name,
                        src_dir_fd=directory,
                        dst_dir_fd=directory,
                        follow_symlinks=False,
                    )
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass

    def entries(
        self, directory: str, *, recursive: bool, skip_directories: frozenset[str] = frozenset(),
        scan_limit: int = MAX_SCAN_ENTRIES,
    ) -> tuple[list[tuple[str, str]], bool]:
        pending = deque([portable_path(directory, allow_root=True)])
        found: list[tuple[str, str]] = []
        count = 0
        while pending:
            parent = pending.popleft()
            with self.directory(parent) as fd, os.scandir(fd) as entries:
                for entry in entries:
                    count += 1
                    if count > scan_limit:
                        return sorted(found), True
                    try:
                        relative = portable_path((PurePosixPath(parent) / entry.name).as_posix())
                        info = entry.stat(follow_symlinks=False)
                    except (ValueError, UnicodeError):
                        continue
                    if stat.S_ISDIR(info.st_mode) and not is_link(info):
                        if entry.name not in skip_directories:
                            found.append((relative, "directory"))
                            if recursive:
                                pending.append(relative)
                    elif _regular(info):
                        found.append((relative, "file"))
        return sorted(found), False


class WindowsWorkspace(PosixWorkspace):
    """Workspace on NTFS. Native commands are stopped before host file operations resume."""

    @contextmanager
    def directory(self, path: str = ".", *, create: bool = False) -> Iterator[Path]:
        parent = self.root
        parts = (".", *PurePosixPath(portable_path(path, allow_root=True)).parts)
        for part in parts:
            parent /= part
            if create:
                parent.mkdir(exist_ok=True)
            info = parent.lstat()
            if is_link(info) or not stat.S_ISDIR(info.st_mode):
                raise ValueError("Use a regular directory inside the workspace.")
        yield parent

    @contextmanager
    def open_file(self, path: str) -> Iterator[BinaryIO]:
        target, info = self._file(path)
        with target.open("rb") as stream:
            actual = os.fstat(stream.fileno())
            if not _regular(actual) or not os.path.samestat(info, actual):
                raise ValueError("The workspace file changed while opening it.")
            yield stream

    def _file(self, path: str) -> tuple[Path, os.stat_result]:
        relative = PurePosixPath(portable_path(path))
        with self.directory(relative.parent.as_posix()) as directory:
            target = directory / relative.name
            info = target.lstat()
        if not _regular(info):
            raise ValueError("The path must point to a regular workspace file.")
        return target, info

    def file_size(self, path: str) -> int:
        return self._file(path)[1].st_size

    def write_bytes(self, path: str, data: bytes, *, overwrite: bool) -> None:
        relative = PurePosixPath(portable_path(path))
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("The file exceeds the workspace file size limit.")
        with self.directory(relative.parent.as_posix(), create=True) as directory:
            target = directory / relative.name
            if overwrite:
                try:
                    if not _regular(target.lstat()):
                        raise ValueError("The destination must be a regular workspace file.")
                except FileNotFoundError:
                    pass
            temporary = directory / f".cook-write-{uuid4().hex}"
            try:
                with temporary.open("xb") as stream:
                    stream.write(data)
                if overwrite:
                    temporary.replace(target)
                else:
                    temporary.rename(target)
            finally:
                temporary.unlink(missing_ok=True)


Workspace = WindowsWorkspace if os.name == "nt" else PosixWorkspace


def list_files(
    workspace: Workspace, directory: str = ".", recursive: bool = False, max_results: int = 80,
    kind: str = "all", cursor: str | None = None,
) -> dict[str, Any]:
    entries, scan_limited = workspace.entries(directory, recursive=recursive)
    limit = max(1, min(max_results, MAX_FIND_RESULTS))
    matches = [
        (path, entry_kind) for path, entry_kind in entries
        if (kind == "all" or kind == entry_kind) and (cursor is None or path > cursor)
    ]
    has_next_page = len(matches) > limit
    return {
        "entries": [{"path": path, "kind": entry_kind} for path, entry_kind in matches[:limit]],
        "truncated": scan_limited or has_next_page,
        "next_cursor": matches[limit - 1][0] if has_next_page else None,
    }


def read_file(
    workspace: Workspace, path: str, start_line: int = 1, line_count: int = 200
) -> dict[str, Any]:
    relative = workspace_path(path)
    raw = workspace.read_bytes(relative, MAX_TEXT_FILE_BYTES)
    if b"\0" in raw:
        raise ValueError("Use run_command to inspect binary files.")
    lines = raw.decode("utf-8").splitlines(keepends=True)
    start = start_line - 1
    end = min(start + min(line_count, 500), len(lines))
    chunks: list[str] = []
    remaining = MAX_READ_CHARS
    next_index = min(start, len(lines))
    clipped_line = False
    for index in range(start, end):
        line = lines[index]
        if len(line) > remaining:
            if not chunks:
                chunks.append(line[:remaining])
                next_index = index + 1
                clipped_line = True
            break
        chunks.append(line)
        remaining -= len(line)
        next_index = index + 1
    return {
        "path": relative,
        "content": "".join(chunks),
        "start_line": start_line,
        "end_line": next_index if chunks else None,
        "total_lines": len(lines),
        "next_line": next_index + 1 if next_index < len(lines) else None,
        "line_truncated": clipped_line,
    }


def write_file(
    workspace: Workspace, path: str, content: str, overwrite: bool = True
) -> dict[str, Any]:
    relative = workspace_path(path)
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise ValueError("The text exceeds the write_file size limit.")
    workspace.write_bytes(relative, data, overwrite=overwrite)
    return {"path": relative, "bytes": len(data)}


def copy_file(
    workspace: Workspace, source: str, destination: str, overwrite: bool = False
) -> dict[str, Any]:
    source = workspace_path(source)
    destination = workspace_path(destination)
    if source == destination:
        raise ValueError("Source and destination must be different files.")
    data = workspace.read_bytes(source)
    workspace.write_bytes(destination, data, overwrite=overwrite)
    return {"source": source, "path": destination, "bytes": len(data)}


def search_files(
    workspace: Workspace,
    directory: str = ".",
    glob: str = "*",
    query: str | None = None,
    case_sensitive: bool = False,
    max_results: int = 60,
) -> dict[str, Any]:
    entries, scan_limited = workspace.entries(
        directory, recursive=True, skip_directories=_SEARCH_SKIPS
    )
    matches: list[dict[str, Any]] = []
    limit = min(max_results, MAX_FIND_RESULTS)
    needle = query if case_sensitive or query is None else query.casefold()
    remaining = 8 * MAX_TEXT_FILE_BYTES

    for path, kind in entries:
        if kind != "file" or not _matches_glob(path, glob):
            continue
        if needle is None:
            matches.append({"path": path})
        else:
            try:
                size = workspace.file_size(path)
                if size > MAX_TEXT_FILE_BYTES:
                    continue
                if size > remaining:
                    return {"matches": matches[:limit], "truncated": True}
                remaining -= size
                data = workspace.read_bytes(path, size)
                if b"\0" in data:
                    continue
                lines = data.decode("utf-8").splitlines()
            except (OSError, ValueError):
                continue
            for number, line in enumerate(lines, 1):
                offset = (line if case_sensitive else line.casefold()).find(needle)
                if offset >= 0:
                    start = max(0, offset - 100)
                    matches.append({"path": path, "line": number, "text": line[start:start + 320]})
                    if len(matches) > limit:
                        break
        if len(matches) > limit:
            return {"matches": matches[:limit], "truncated": True}
    return {"matches": matches, "truncated": scan_limited}


def _matches_glob(path: str, glob: str) -> bool:
    return fnmatchcase(path, glob) or (glob.startswith("**/") and fnmatchcase(path, glob[3:]))


def upload(workspace: Workspace, path: str, content: str, *, overwrite: bool = False) -> dict[str, Any]:
    data = base64.b64decode(content, validate=True)
    workspace.write_bytes(path, data, overwrite=overwrite)
    return {"bytes": len(data)}


def download(workspace: Workspace, path: str) -> dict[str, Any]:
    data = workspace.read_bytes(path)
    return {"content": base64.b64encode(data).decode("ascii")}
