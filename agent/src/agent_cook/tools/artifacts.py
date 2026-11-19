import mimetypes
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from uuid import uuid4

from agent_cook.protocol.artifacts import ArtifactRef
from agent_cook.sandbox.interface import Sandbox, SandboxError
from agent_cook.sandbox.limits import MAX_TRANSFER_BYTES
from agent_cook.sandbox.paths import workspace_path
from agent_cook.tools.spec import SubmittedFile, ToolError

SUBMIT_RESULTS = "submit_results"


async def stale_submissions(sandbox: Sandbox, files: Iterable[SubmittedFile]) -> list[str]:
    stale = []
    for file in files:
        try:
            data = await sandbox.get_file(file.workspace_path)
        except SandboxError:
            stale.append(file.workspace_path)
        else:
            if sha256(data).hexdigest() != file.sha256:
                stale.append(file.workspace_path)
    return stale


class ArtifactExporter:
    """Publish a complete batch of explicitly selected workspace files."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    async def export(self, sandbox: Sandbox, paths: list[str]) -> tuple[ArtifactRef, ...]:
        return tuple(file.artifact for file in await self.submit(sandbox, paths))

    async def submit(self, sandbox: Sandbox, paths: list[str]) -> tuple[SubmittedFile, ...]:
        try:
            selected = [workspace_path(path) for path in paths]
        except ValueError as error:
            raise ToolError(str(error)) from error
        if len(selected) != len(set(selected)):
            raise ToolError("List each result file once.")
        if not selected:
            raise ToolError("Provide at least one result file.")

        self._directory.mkdir(parents=True, exist_ok=True)
        submission = uuid4().hex
        destination = self._directory / submission
        total = 0
        files = []
        with TemporaryDirectory(prefix=".pending-", dir=self._directory) as staging:
            stage = Path(staging)
            for index, path in enumerate(selected, 1):
                target = stage.joinpath(*PurePosixPath(path).parts)
                if not target.is_relative_to(stage):
                    raise ToolError("The result path is not valid on this host.")
                data = await sandbox.get_file(path)
                total += len(data)
                if total > MAX_TRANSFER_BYTES:
                    raise ToolError("The result batch exceeds the export limit. Submit fewer files.")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(data)
                artifact = ArtifactRef(
                    id=f"{submission}-{index}",
                    path=(PurePosixPath(self._directory.name) / submission / path).as_posix(),
                    media_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
                )
                files.append(SubmittedFile(path, sha256(data).hexdigest(), artifact))
            stage.rename(destination)
        return tuple(files)
