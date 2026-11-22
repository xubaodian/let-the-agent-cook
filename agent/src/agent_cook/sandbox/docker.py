import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from uuid import uuid4

from agent_cook.retry import retry_transient
from agent_cook.sandbox.client import WorkerSandbox
from agent_cook.sandbox.docker_setup import docker_command, docker_transport_failed
from agent_cook.sandbox.interface import Sandbox, SandboxError, SandboxTransportError
from agent_cook.sandbox.limits import MAX_RPC_BYTES, WORKER_MODULE, WORKER_PYTHON, WORKSPACE_ROOT

DEFAULT_DOCKER_IMAGE = "agent-cook-sandbox:local"


async def _docker(
    *args: str, payload: bytes | None = None, timeout_seconds: float = 15
) -> tuple[int, bytes, bytes]:
    try:
        process = await asyncio.create_subprocess_exec(
            docker_command(),
            *args,
            stdin=asyncio.subprocess.PIPE if payload is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise SandboxError("Docker is required for the local sandbox backend.") from error
    try:
        async with asyncio.timeout(timeout_seconds):
            stdout, stderr = await process.communicate(payload)
    except BaseException as error:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            with suppress(OSError, TimeoutError):
                async with asyncio.timeout(5):
                    await process.communicate()
        if isinstance(error, (TimeoutError, ConnectionError)):
            raise SandboxTransportError("The Docker sandbox transport was interrupted.") from error
        raise
    if len(stdout) > MAX_RPC_BYTES:
        raise SandboxError("The Docker worker response exceeds the transfer limit.")
    return process.returncode, stdout, stderr


def _failure(message: str, stderr: bytes) -> SandboxError:
    detail = stderr.decode("utf-8", errors="replace").strip()
    error_type = SandboxTransportError if docker_transport_failed(detail) else SandboxError
    return error_type(f"{message} {detail[:400]}".rstrip())


def _can_retry(error: Exception) -> bool:
    return isinstance(error, SandboxTransportError)


class DockerSandboxFactory:
    def __init__(self, image: str = DEFAULT_DOCKER_IMAGE, *, allow_network: bool = False) -> None:
        if not image.strip() or image.startswith("-"):
            raise ValueError("Provide a Docker image name.")
        self._image = image
        self._network = "bridge" if allow_network else "none"
        self.supports_dependency_installation = allow_network

    async def reclaim(self, run_id: str) -> None:
        for label in (run_id, f"{run_id}-web"):
            async def containers() -> bytes:
                code, stdout, stderr = await _docker("ps", "-aq", "--filter", f"label=agent-cook.run={label}")
                if code:
                    raise _failure("Docker could not list containers for this run.", stderr)
                return stdout

            stdout = await retry_transient(containers, _can_retry)
            for container in stdout.decode("ascii").splitlines():
                if not re.fullmatch(r"[a-f0-9]{12,64}", container):
                    raise SandboxError("Docker returned an invalid container ID.")
                await self._remove(container)

    @asynccontextmanager
    async def open(self, run_id: str) -> AsyncIterator[Sandbox]:
        name = f"agent-cook-{uuid4().hex}"
        attempted = False

        async def exchange(payload: bytes, timeout: float) -> bytes:
            code, stdout, stderr = await _docker(
                "exec", "-i", "--workdir", WORKSPACE_ROOT,
                name, WORKER_PYTHON, "-I", "-m", WORKER_MODULE,
                payload=payload, timeout_seconds=timeout,
            )
            if code:
                raise _failure("Docker could not run the workspace worker.", stderr)
            return stdout

        async def start() -> None:
            nonlocal attempted
            if attempted:
                # The engine may have accepted a start whose response was lost.
                try:
                    await self._remove(name)
                except SandboxError as error:
                    raise SandboxError("Docker could not clear the task container before retrying.") from error
            attempted = True
            code, _, stderr = await _docker(
                "run", "--detach", "--rm", "--init", "--pull=never",
                "--name", name, "--label", f"agent-cook.run={run_id}",
                "--network", self._network, "--cap-drop=ALL",
                "--security-opt=no-new-privileges=true",
                "--memory=1g", "--cpus=2", "--pids-limit=256", "--shm-size=128m",
                "--user=10001:10001", "--workdir", WORKSPACE_ROOT,
                self._image, "sleep", "infinity",
            )
            if code:
                raise _failure(f"Docker could not start image '{self._image}'.", stderr)

        try:
            await retry_transient(start, _can_retry)
            yield WorkerSandbox(exchange)
        except BaseException:
            with suppress(SandboxError):
                await self._remove(name)
            raise
        else:
            await self._remove(name)

    @staticmethod
    async def _remove(name: str) -> None:
        async def remove() -> None:
            code, _, stderr = await _docker("rm", "--force", name, timeout_seconds=4)
            if code and b"No such container" not in stderr:
                raise _failure("Docker could not remove the task container.", stderr)

        try:
            async with asyncio.timeout(15):
                await retry_transient(remove, _can_retry)
        except TimeoutError as error:
            raise SandboxError("Docker timed out removing the task container.") from error
