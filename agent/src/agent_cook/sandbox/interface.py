from collections.abc import Sequence
from dataclasses import dataclass
from typing import AsyncContextManager, Protocol


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str


class Sandbox(Protocol):
    """I/O within a task workspace."""

    async def exec(
        self, argv: Sequence[str], *, cwd: str, timeout_seconds: float
    ) -> CommandResult: ...

    async def put_file(self, path: str, content: bytes) -> None: ...

    async def get_file(self, path: str) -> bytes: ...


class SandboxFactory(Protocol):
    def open(self, run_id: str) -> AsyncContextManager[Sandbox]:
        """Acquire a sandbox and release it on context exit, including errors."""
        ...
