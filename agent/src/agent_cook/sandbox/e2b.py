import asyncio
import shlex
from asyncio import sleep
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from functools import partial
from typing import TYPE_CHECKING
from uuid import uuid4

from agent_cook.retry import retry_transient
from agent_cook.sandbox.client import WorkerSandbox
from agent_cook.sandbox.interface import Sandbox, SandboxError, SandboxTransportError
from agent_cook.sandbox.limits import MAX_RPC_BYTES, WORKER_MODULE, WORKER_PYTHON, WORKSPACE_ROOT

if TYPE_CHECKING:
    from e2b import AsyncSandbox

_WORKER_USER = "cook"
_LEASE_SECONDS = 600


def _status_code(error: Exception) -> int | None:
    return getattr(error, "status_code", None) or getattr(getattr(error, "response", None), "status_code", None)


def _retryable_create(error: Exception) -> bool:
    from e2b import exceptions
    from httpx import ConnectError, ConnectTimeout, PoolTimeout

    # Provision only after a rejected request or a failed connection before it was sent.
    return (
        _status_code(error) == 429
        or isinstance(error, (ConnectError, ConnectTimeout, PoolTimeout, exceptions.RateLimitException))
        or isinstance(error, getattr(exceptions, "ServiceBusyException", ()))
    )


def _retryable_io(error: Exception) -> bool:
    from e2b import exceptions
    from httpx import NetworkError, RemoteProtocolError, TimeoutException

    if isinstance(error, getattr(exceptions, "SandboxNotRunningException", ())):
        return False
    return _status_code(error) in (408, 429, 500, 502, 503, 504) or isinstance(error, (
        ConnectionError, TimeoutError, NetworkError, RemoteProtocolError, TimeoutException,
        exceptions.RateLimitException, exceptions.TimeoutException,
    ))


class E2BSandboxFactory:
    def __init__(
        self, template: str, *, api_key: str | None = None, allow_network: bool = False,
        lifetime_seconds: int | None = None,
    ) -> None:
        if not template.strip():
            raise ValueError("Set an E2B template with the workspace worker installed.")
        self._template = template
        self._api_key = api_key
        self._allow_network = allow_network
        self._lifetime_seconds = lifetime_seconds
        self.supports_dependency_installation = allow_network

    @asynccontextmanager
    async def open(self, run_id: str) -> AsyncIterator[Sandbox]:
        try:
            from e2b import AsyncSandbox
        except ImportError as error:
            raise SandboxError("Install the agent project's e2b extra for remote runs.") from error

        try:
            session = await retry_transient(
                partial(
                    AsyncSandbox.create,
                    template=self._template,
                    api_key=self._api_key,
                    timeout=self._lifetime_seconds if self._lifetime_seconds is not None else _LEASE_SECONDS,
                    allow_internet_access=self._allow_network,
                    network={"allow_public_traffic": False},
                    metadata={"agent_cook_run": run_id},
                ),
                _retryable_create,
            )
        except Exception as error:
            raise SandboxError(f"E2B could not open the task sandbox ({type(error).__name__}).") from error

        renewal = asyncio.create_task(self._keep_alive(session)) if self._lifetime_seconds is None else None

        async def exchange(payload: bytes, timeout: float) -> bytes:
            if renewal is not None and renewal.done():
                renewal.result()
            prefix = f"/tmp/agent-cook-{uuid4().hex}"
            request_path = f"{prefix}.request"
            response_path = f"{prefix}.response"
            args = [
                WORKER_PYTHON, "-I", "-m", WORKER_MODULE,
                "--request", request_path, "--response", response_path,
            ]

            async def read_response() -> bytes:
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

            try:
                await retry_transient(
                    partial(session.files.write, request_path, payload, user=_WORKER_USER, request_timeout=30),
                    _retryable_io,
                )
                try:
                    await session.commands.run(
                        shlex.join(args), cwd=WORKSPACE_ROOT, user=_WORKER_USER, timeout=timeout,
                    )
                except Exception as error:
                    if _retryable_io(error):
                        raise SandboxTransportError(
                            f"E2B lost its connection to the workspace worker ({type(error).__name__})."
                        ) from error
                    raise
                return await retry_transient(read_response, _retryable_io)
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
            try:
                yield WorkerSandbox(exchange)
            finally:
                if renewal is not None:
                    renewal.cancel()
                    await asyncio.gather(renewal, return_exceptions=True)
        except BaseException:
            with suppress(Exception):
                await self._kill(session)
            raise
        else:
            await self._kill(session)

    @staticmethod
    async def _keep_alive(session: "AsyncSandbox") -> None:
        while True:
            await sleep(_LEASE_SECONDS / 2)
            try:
                async with asyncio.timeout(15):
                    await retry_transient(partial(session.set_timeout, _LEASE_SECONDS, request_timeout=4), _retryable_io)
            except Exception as error:
                raise SandboxError(f"E2B could not renew the task sandbox lease ({type(error).__name__}).") from error

    @staticmethod
    async def _kill(session: "AsyncSandbox") -> None:
        try:
            async with asyncio.timeout(15):
                await retry_transient(partial(session.kill, request_timeout=4), _retryable_io)
        except Exception as error:
            raise SandboxError(f"E2B could not close the task sandbox ({type(error).__name__}).") from error
