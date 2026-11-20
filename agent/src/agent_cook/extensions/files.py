from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from agent_cook.local_files import LocalTree, PortablePaths, portable_path
from agent_cook.sandbox.interface import SandboxError
from agent_cook.sandbox.limits import MAX_FILE_BYTES, MAX_SCAN_ENTRIES, MAX_TRANSFER_BYTES


@dataclass(frozen=True, slots=True)
class FileTree:
    source: LocalTree
    directory: str
    required_file: str | None = None
    exclude: frozenset[str] = frozenset()


def collect_workspace_files(inputs: Mapping[str, bytes], trees: Iterable[FileTree]) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    index = PortablePaths()
    remaining_bytes = MAX_TRANSFER_BYTES
    remaining_entries = MAX_SCAN_ENTRIES

    def add(path: str, content: bytes) -> None:
        nonlocal remaining_bytes
        try:
            path = portable_path(path)
            index.add(path)
        except ValueError as error:
            raise SandboxError(str(error)) from error
        if path in files:
            raise SandboxError(f"Workspace path '{path}' conflicts with another input.")
        if len(content) > min(MAX_FILE_BYTES, remaining_bytes):
            raise SandboxError(f"Workspace file '{path}' exceeds the file or batch transfer limit.")
        files[path] = content
        remaining_bytes -= len(content)

    for path, content in inputs.items():
        add(path, content)
    for tree in trees:
        try:
            entries, limited = tree.source.entries(
                tree.directory, recursive=True, scan_limit=remaining_entries,
            )
        except (OSError, ValueError) as error:
            raise SandboxError(f"Could not list input files in '{tree.directory}': {error}") from error
        if limited:
            raise SandboxError("Configured package files exceed the workspace file scan limit.")
        if tree.required_file and (tree.required_file, "file") not in entries:
            raise SandboxError(f"Package is missing its entry '{tree.required_file}'.")
        remaining_entries -= len(entries)
        for path, kind in entries:
            if kind != "file" or path in tree.exclude:
                continue
            try:
                content = tree.source.read_bytes(path, min(MAX_FILE_BYTES, remaining_bytes))
            except OSError as error:
                raise SandboxError(f"Could not read workspace file '{path}'.") from error
            except ValueError as error:
                raise SandboxError(f"Could not transfer workspace file '{path}': {error}") from error
            add(path, content)
    return files
