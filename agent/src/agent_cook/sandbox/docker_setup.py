import os
import sys
from pathlib import Path
from shutil import which
from subprocess import DEVNULL, PIPE, STDOUT, Popen, TimeoutExpired, run
from time import sleep

from agent_cook.retry import RETRY_DELAYS
from agent_cook.sandbox.interface import SandboxError


class DockerSetupError(SandboxError):
    pass


def install_instructions() -> str:
    if sys.platform == "win32":
        return (
            "Install or start Docker Desktop and switch to Linux containers. "
            "https://docs.docker.com/desktop/setup/install/windows-install/"
        )
    if sys.platform == "darwin":
        return "Install or start Docker Desktop. https://docs.docker.com/desktop/setup/install/mac-install/"
    return (
        "Install or start Docker Engine and ensure your account can run docker info. "
        "https://docs.docker.com/engine/install/"
    )


def docker_command() -> str:
    if executable := which("docker"):
        return executable
    if sys.platform == "win32":
        for name in ("ProgramW6432", "ProgramFiles", "LOCALAPPDATA"):
            if directory := os.environ.get(name):
                path = Path(directory) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe"
                if path.is_file():
                    return str(path)
    if sys.platform == "darwin":
        for path in (
            Path("/Applications/Docker.app/Contents/Resources/bin/docker"),
            Path.home() / ".docker/bin/docker",
        ):
            if path.is_file():
                return str(path)
    return "docker"


def docker_transport_failed(detail: str) -> bool:
    detail = detail.lower()
    return "permission denied" not in detail and any(marker in detail for marker in (
        "cannot connect to the docker daemon",
        "connection refused",
        "connection reset by peer",
        "i/o timeout",
        "context deadline exceeded",
        "503 service unavailable",
        "502 bad gateway",
        "504 gateway timeout",
    ))


def _check(executable: str, *args: str):
    for delay in (*RETRY_DELAYS, None):
        try:
            result = run(
                [executable, *args], stdin=DEVNULL, capture_output=True,
                timeout=12, encoding="utf-8", errors="replace",
            )
        except TimeoutExpired:
            if delay is None:
                raise
        else:
            if delay is None or not result.returncode or not docker_transport_failed(result.stderr):
                return result
        sleep(delay)


def check_linux_docker() -> str:
    executable = docker_command()
    try:
        host = os.environ.get("DOCKER_HOST", "") if not os.environ.get("DOCKER_CONTEXT") else ""
        if not host:
            context = _check(executable, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}")
            if context.returncode:
                raise DockerSetupError(f"Docker has no usable context. {install_instructions()}")
            host = context.stdout.strip()
        if not host.startswith(("unix:///", "npipe:////./pipe/")):
            raise DockerSetupError("Choose a local Docker context before copying files into a Docker workspace.")
        info = _check(executable, "info", "--format", "{{.OSType}}")
    except FileNotFoundError as error:
        raise DockerSetupError(f"Docker was not found. {install_instructions()}") from error
    except (OSError, TimeoutExpired) as error:
        raise DockerSetupError(f"Docker did not respond. {install_instructions()}") from error
    if info.returncode:
        raise DockerSetupError(f"Docker is installed but its engine is not reachable. {install_instructions()}")
    if info.stdout.strip().lower() != "linux":
        raise DockerSetupError(f"The workspace needs Docker in Linux container mode. {install_instructions()}")
    return executable


def image_available(executable: str, image: str) -> bool:
    try:
        return _check(executable, "image", "inspect", image).returncode == 0
    except (OSError, TimeoutExpired) as error:
        raise DockerSetupError(f"Could not inspect the sandbox image '{image}'.") from error


def prepare_docker(image: str, *, pull: bool) -> None:
    executable = check_linux_docker()
    if image_available(executable, image):
        return
    if not pull:
        raise DockerSetupError(
            f"Docker image '{image}' is missing. Build it with ./cook setup --docker "
            "or select an installed workspace image with --docker-image."
        )
    print(f"Downloading Docker workspace {image} …", file=sys.stderr, flush=True)
    try:
        with Popen(
            [executable, "pull", image], stdin=DEVNULL, stdout=PIPE, stderr=STDOUT,
            encoding="utf-8", errors="replace",
        ) as process:
            try:
                for line in process.stdout:
                    print(line.rstrip(), file=sys.stderr, flush=True)
            except KeyboardInterrupt:
                process.terminate()
                raise
            if process.wait():
                raise DockerSetupError(f"Could not download '{image}'. Check Docker and your connection, then retry.")
    except OSError as error:
        raise DockerSetupError(f"Could not start the download of '{image}'.") from error
