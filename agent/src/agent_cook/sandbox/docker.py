import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from uuid import uuid4

from agent_cook.sandbox.client import WorkerSandbox
from agent_cook.sandbox.interface import Sandbox, SandboxError
from agent_cook.sandbox.limits import MAX_RPC_BYTES, WORKER_MODULE, WORKER_PYTHON, WORKSPACE_ROOT

DEFAULT_DOCKER_IMAGE = "agent-cook-sandbox:local"


async def _docker(
    *args: str, payload: bytes | None = None, timeout_seconds: float = 15
) -> tuple[int, bytes, bytes]:
    try:
        process = await asyncio.create_subprocess_exec(
            "docker",
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
        if isinstance(error, TimeoutError):
            raise SandboxError("The Docker sandbox transport timed out.") from error
        raise
    if len(stdout) > MAX_RPC_BYTES:
        raise SandboxError("The Docker worker response exceeds the transfer limit.")
    return process.returncode, stdout, stderr


class DockerSandboxFactory:
    def __init__(self, image: str = DEFAULT_DOCKER_IMAGE, *, allow_network: bool = False) -> None:
        if not image.strip() or image.startswith("-"):
            raise ValueError("Provide a Docker image name.")
        self._image = image
        self._network = "bridge" if allow_network else "none"

    async def reclaim(self, run_id: str) -> None:
        for label in (run_id, f"{run_id}-web"):
            code, stdout, _ = await _docker("ps", "-aq", "--filter", f"label=agent-cook.run={label}")
            if code:
                raise SandboxError("Docker could not list containers for this run.")
            for container in stdout.decode("ascii").splitlines():
                if not re.fullmatch(r"[a-f0-9]{12,64}", container):
                    raise SandboxError("Docker returned an invalid container ID.")
                await self._remove(container)

    @asynccontextmanager
    async def open(self, run_id: str) -> AsyncIterator[Sandbox]:
        name = f"agent-cook-{uuid4().hex}"

        async def exchange(payload: bytes, timeout: float) -> bytes:
            code, stdout, stderr = await _docker(
                "exec", "-i", "--workdir", WORKSPACE_ROOT,
                name, WORKER_PYTHON, "-I", "-m", WORKER_MODULE,
                payload=payload, timeout_seconds=timeout,
            )
            if code:
                detail = stderr.decode("utf-8", errors="replace").strip()[:400]
                raise SandboxError(f"Docker could not run the workspace worker. {detail}")
            return stdout

        try:
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
                detail = stderr.decode("utf-8", errors="replace").strip()[:400]
                raise SandboxError(f"Docker could not start image '{self._image}'. {detail}")
            yield WorkerSandbox(exchange)
        except BaseException:
            with suppress(SandboxError):
                await self._remove(name)
            raise
        else:
            await self._remove(name)

    @staticmethod
    async def _remove(name: str) -> None:
        code, _, stderr = await _docker("rm", "--force", name)
        if code and b"No such container" not in stderr:
            raise SandboxError("Docker could not remove the task container.")
