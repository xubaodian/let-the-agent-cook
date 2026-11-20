import os
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from agent_cook.core.types import RunResult
from agent_cook.local_files import LocalTree, PortablePaths, portable_path
from agent_cook.sandbox.limits import MAX_FILE_BYTES, MAX_TRANSFER_BYTES


def _exported_files(result: RunResult) -> dict[str, str]:
    index = PortablePaths()
    files: dict[str, str] = {}
    for artifact in result.artifacts:
        path = PurePosixPath(portable_path(artifact.path))
        if len(path.parts) < 3 or path.parts[0] != f"{result.run_id}-artifacts":
            raise ValueError(f"Result '{artifact.path}' does not belong to this run's export.")
        relative = PurePosixPath(*path.parts[2:]).as_posix()
        index.add(relative)
        files[relative] = path.as_posix()
    return files


def publish_results(trace: Path, result: RunResult) -> Path | None:
    if not result.artifacts:
        return None
    source = LocalTree(trace.parent)
    root = source.root
    destination = root / f"{result.run_id}-results"
    if os.path.lexists(destination):
        raise FileExistsError(f"The results directory '{destination}' already exists.")

    with TemporaryDirectory(prefix=f".{result.run_id}-results-", dir=root) as staging:
        stage = Path(staging)
        for relative, exported in _exported_files(result).items():
            target = stage.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes(exported))
        stage.rename(destination)
    return destination


def result_snapshots(trace: Path, result: RunResult) -> dict[str, bytes]:
    source = LocalTree(trace.parent)
    remaining = MAX_TRANSFER_BYTES
    snapshots = {}
    for relative, exported in _exported_files(result).items():
        data = source.read_bytes(exported, min(MAX_FILE_BYTES, remaining))
        snapshots[relative] = data
        remaining -= len(data)
    return snapshots
