import asyncio
import shlex
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING
from uuid import uuid4

from agent_cook.sandbox.client import WorkerSandbox
from agent_cook.sandbox.interface import Sandbox, SandboxError
from agent_cook.sandbox.limits import MAX_RPC_BYTES, WORKER_MODULE, WORKER_PYTHON, WORKSPACE_ROOT

if TYPE_CHECKING:
    from e2b import AsyncSandbox

_WORKER_USER = "cook"


class E2BSandboxFactory:
    def __init__(
        self, template: str, *, allow_network: bool = False, lifetime_seconds: int = 300
    ) -> None:
        if not template.strip():
            raise ValueError("Set an E2B template with the workspace worker installed.")
        self._template = template
        self._allow_network = allow_network
        self._lifetime_seconds = lifetime_seconds

    @asynccontextmanager
    async def open(self, run_id: str) -> AsyncIterator[Sandbox]:
        try:
            from e2b import AsyncSandbox
        except ImportError as error:
            raise SandboxError("Install the agent project's e2b extra for remote runs.") from error

        try:
            session = await AsyncSandbox.create(
                template=self._template,
                timeout=self._lifetime_seconds,
                allow_internet_access=self._allow_network,
                network={"allow_public_traffic": False},
                metadata={"agent_cook_run": run_id},
            )
        except Exception as error:
            raise SandboxError(f"E2B could not open the task sandbox ({type(error).__name__}).") from error

        async def exchange(payload: bytes, timeout: float) -> bytes:
            prefix = f"/tmp/agent-cook-{uuid4().hex}"
            request_path = f"{prefix}.request"
            response_path = f"{prefix}.response"
            args = [
                WORKER_PYTHON, "-I", "-m", WORKER_MODULE,
                "--request", request_path, "--response", response_path,
            ]
            try:
                await session.files.write(request_path, payload, user=_WORKER_USER, request_timeout=30)
                await session.commands.run(
                    shlex.join(args), cwd=WORKSPACE_ROOT, user=_WORKER_USER, timeout=timeout
                )
                download = await session.files.read(
                    response_path, format="stream", user=_WORKER_USER, request_timeout=30,
                )
                output = bytearray()
                async with download:
                    async for chunk in download:
                        if len(output) + len(chunk) > MAX_RPC_BYTES:
                            raise SandboxError("The E2B worker response exceeds the transfer limit.")
                        output.extend(chunk)
                return bytes(output)
            except SandboxError:
                raise
            except Exception as error:
                raise SandboxError(
                    f"E2B could not exchange data with the workspace worker ({type(error).__name__})."
                ) from error
            finally:
                # Removing these staging files is best effort. The Run owns the entire sandbox lease.
                await asyncio.gather(
                    session.files.remove(request_path, user=_WORKER_USER, request_timeout=5),
                    session.files.remove(response_path, user=_WORKER_USER, request_timeout=5),
                    return_exceptions=True,
                )

        try:
            yield WorkerSandbox(exchange)
        except BaseException:
            with suppress(Exception):
                await self._kill(session)
            raise
        else:
            await self._kill(session)

    @staticmethod
    async def _kill(session: "AsyncSandbox") -> None:
        try:
            async with asyncio.timeout(15):
                await session.kill()
        except Exception as error:
            raise SandboxError(f"E2B could not close the task sandbox ({type(error).__name__}).") from error
