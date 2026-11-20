from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, AsyncContextManager, Protocol


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False


class SandboxError(RuntimeError):
    """The backend or a workspace operation could not complete."""


class Sandbox(Protocol):
    """I/O within a task workspace."""

    async def exec(
        self, argv: Sequence[str], *, cwd: str, timeout_seconds: float
    ) -> CommandResult: ...

    async def put_file(self, path: str, content: bytes, *, overwrite: bool = False) -> None: ...

    async def get_file(self, path: str) -> bytes: ...

    async def request(
        self, operation: str, arguments: dict[str, Any], *, timeout_seconds: float = 15
    ) -> dict[str, Any]: ...


class SandboxFactory(Protocol):
    def open(self, run_id: str) -> AsyncContextManager[Sandbox]:
        """Acquire a sandbox and release it on context exit, including errors."""
        ...
