import base64
import json
from collections.abc import Awaitable, Callable, Sequence
from functools import partial
from typing import Any

from agent_cook.retry import retry_transient
from agent_cook.sandbox.interface import CommandResult, SandboxError, SandboxTransportError
from agent_cook.sandbox.limits import MAX_FILE_BYTES, MAX_RPC_BYTES, WORKER_VERSION
from agent_cook.sandbox.paths import workspace_path

_READ_OPERATIONS = frozenset({"download", "list_files", "read_file", "search_files"})
_LEGACY_COMMAND_LIMIT = 120
_LEGACY_COMMAND_LIMIT_ERROR = "Command timeout must be between 0 and 120 seconds."


class WorkerSandbox:
    """Send the same workspace operations through a backend's byte transport."""

    def __init__(self, exchange: Callable[[bytes, float], Awaitable[bytes]]) -> None:
        self._exchange = exchange
        self._legacy_command_limit = False

    async def request(
        self, operation: str, arguments: dict[str, Any], *, timeout_seconds: float = 15
    ) -> dict[str, Any]:
        payload = json.dumps(
            {"version": WORKER_VERSION, "operation": operation, "arguments": arguments},
            allow_nan=False,
        ).encode("utf-8")
        if len(payload) > MAX_RPC_BYTES:
            raise SandboxError("The workspace request exceeds the transfer limit.")
        exchange = partial(self._exchange, payload, timeout_seconds)
        if operation in _READ_OPERATIONS:
            raw = await retry_transient(exchange, lambda error: isinstance(error, SandboxTransportError))
        else:
            raw = await exchange()
        if len(raw) > MAX_RPC_BYTES:
            raise SandboxError("The workspace response exceeds the transfer limit.")
        try:
            response = json.loads(raw)
        except (UnicodeError, ValueError) as error:
            raise SandboxError("The sandbox returned an invalid worker response.") from error
        if not isinstance(response, dict) or response.get("version") != WORKER_VERSION:
            raise SandboxError("The workspace worker version does not match this client.")
        if response.get("ok") is not True:
            raise SandboxError(str(response.get("error", "Workspace operation failed."))[:600])
        result = response.get("result")
        if not isinstance(result, dict):
            raise SandboxError("The workspace worker returned an invalid result.")
        return result

    async def exec(
        self, argv: Sequence[str], *, cwd: str, timeout_seconds: float
    ) -> CommandResult:
        if self._legacy_command_limit:
            timeout_seconds = min(timeout_seconds, _LEGACY_COMMAND_LIMIT)
        arguments = {"argv": list(argv), "cwd": cwd, "timeout_seconds": timeout_seconds}
        try:
            result = await self.request("run_command", arguments, timeout_seconds=timeout_seconds + 10)
        except SandboxError as error:
            if timeout_seconds <= _LEGACY_COMMAND_LIMIT or str(error) != _LEGACY_COMMAND_LIMIT_ERROR:
                raise
            # Old images reject this value during validation, before starting the command.
            self._legacy_command_limit = True
            arguments["timeout_seconds"] = _LEGACY_COMMAND_LIMIT
            result = await self.request("run_command", arguments, timeout_seconds=_LEGACY_COMMAND_LIMIT + 10)
        return CommandResult(
            exit_code=result["exit_code"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            timed_out=result["timed_out"],
            truncated=result["truncated"],
        )

    async def put_file(self, path: str, content: bytes, *, overwrite: bool = False) -> None:
        if len(content) > MAX_FILE_BYTES:
            raise SandboxError("The input file exceeds the per-file transfer limit.")
        arguments: dict[str, Any] = {"path": workspace_path(path), "content": base64.b64encode(content).decode("ascii")}
        if overwrite:
            arguments["overwrite"] = True
        await self.request("upload", arguments, timeout_seconds=30)

    async def get_file(self, path: str) -> bytes:
        result = await self.request("download", {"path": workspace_path(path)}, timeout_seconds=30)
        try:
            content = base64.b64decode(result["content"], validate=True)
        except (KeyError, TypeError, ValueError) as error:
            raise SandboxError("The workspace worker returned an invalid file.") from error
        if len(content) > MAX_FILE_BYTES:
            raise SandboxError("The result file exceeds the per-file transfer limit.")
        return content
