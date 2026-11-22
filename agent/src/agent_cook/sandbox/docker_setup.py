import os
import sys
import webbrowser
from pathlib import Path
from shutil import which
from subprocess import DEVNULL, PIPE, STDOUT, Popen, TimeoutExpired, run
from time import monotonic, sleep

from agent_cook.retry import RETRY_DELAYS
from agent_cook.sandbox.interface import SandboxError


class DockerSetupError(SandboxError):
    def __init__(self, message: str, *, startable: bool = False) -> None:
        super().__init__(message)
        self.startable = startable


def _install_url() -> str:
    if sys.platform == "win32":
        return "https://docs.docker.com/desktop/setup/install/windows-install/"
    if sys.platform == "darwin":
        return "https://docs.docker.com/desktop/setup/install/mac-install/"
    return "https://docs.docker.com/engine/install/"


def _windows_desktop_roots() -> tuple[Path, ...]:
    roots = tuple(Path(directory) / "Docker" / "Docker" for name in ("ProgramW6432", "ProgramFiles")
                  if (directory := os.environ.get(name)))
    if local := os.environ.get("LOCALAPPDATA"):
        roots += (Path(local) / "Programs" / "DockerDesktop", Path(local) / "Docker" / "Docker")
    return roots


def docker_command() -> str:
    if executable := which("docker"):
        return executable
    if sys.platform == "win32":
        for directory in _windows_desktop_roots():
            path = directory / "resources/bin/docker.exe"
            if path.is_file():
                return str(path)
    if sys.platform == "darwin":
        for path in (
            Path("/Applications/Docker.app/Contents/Resources/bin/docker"),
            Path.home() / ".docker/bin/docker",
            Path("/opt/homebrew/bin/docker"),
            Path("/usr/local/bin/docker"),
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


def _check(executable: str, *args: str, retry: bool = True):
    for delay in (*RETRY_DELAYS, None) if retry else (None,):
        try:
            result = run(
                [executable, *args], stdin=DEVNULL, capture_output=True,
                timeout=12 if retry else 3, encoding="utf-8", errors="replace",
            )
        except TimeoutExpired:
            if delay is None:
                raise
        else:
            if delay is None or not result.returncode or not docker_transport_failed(result.stderr):
                return result
        sleep(delay)


def check_linux_docker(*, retry: bool = True) -> str:
    executable = docker_command()
    try:
        host = os.environ.get("DOCKER_HOST", "") if not os.environ.get("DOCKER_CONTEXT") else ""
        if not host:
            context = _check(executable, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}", retry=retry)
            if context.returncode:
                raise DockerSetupError("Docker has no usable context. Select a local context and retry.")
            host = context.stdout.strip()
        if not host.startswith(("unix:///", "npipe:////./pipe/")):
            raise DockerSetupError("Choose a local Docker context before copying files into a Docker workspace.")
        info = _check(executable, "info", "--format", "{{.OSType}}", retry=retry)
    except FileNotFoundError as error:
        raise DockerSetupError(f"Docker was not found. Run agent-cook docker to prepare it. {_install_url()}",
                               startable=True) from error
    except (OSError, TimeoutExpired) as error:
        raise DockerSetupError("Docker did not respond. Run agent-cook docker to prepare it.", startable=True) from error
    if info.returncode:
        if "permission denied" in info.stderr.lower():
            raise DockerSetupError("Docker denied access. Verify this user can run docker info, then retry.")
        raise DockerSetupError("Docker's engine is not reachable. Run agent-cook docker to prepare it.", startable=True)
    if info.stdout.strip().lower() != "linux":
        raise DockerSetupError("Switch Docker to Linux containers and retry the task.")
    return executable


def _context_name() -> str:
    if context := os.environ.get("DOCKER_CONTEXT"):
        return context
    try:
        result = _check(docker_command(), "context", "show", retry=False)
    except (OSError, TimeoutExpired):
        return ""
    return result.stdout.strip() if not result.returncode else ""


def _start_command() -> list[str] | None:
    if sys.platform == "win32":
        for directory in _windows_desktop_roots():
            desktop = directory / "Docker Desktop.exe"
            if desktop.is_file():
                return [str(desktop)]
        return None

    context = _context_name()
    if sys.platform == "darwin":
        host = os.environ.get("DOCKER_HOST", "") if not os.environ.get("DOCKER_CONTEXT") else ""
        profile = context.removeprefix("colima-") if context.startswith("colima-") else None
        if context == "colima":
            profile = "default"
        if host.startswith("unix:///") and "/.colima/" in host:
            name = Path(host.removeprefix("unix://")).parent.name
            profile = "default" if name == ".colima" else name
        desktop = any(
            path.is_dir() for path in (Path("/Applications/Docker.app"), Path.home() / "Applications/Docker.app")
        )
        if profile or (not desktop and context in ("", "default")):
            colima = which("colima") or next((
                str(path) for path in (Path("/opt/homebrew/bin/colima"), Path("/usr/local/bin/colima"))
                if path.is_file()
            ), None)
            if colima:
                return [colima, "start", "--", profile] if profile and profile != "default" else [colima, "start"]
        return ["open", "-a", "Docker"] if desktop and not profile else None

    if context == "desktop-linux":
        return [docker_command(), "desktop", "start"]
    if systemctl := which("systemctl"):
        rootless = context == "rootless" or os.environ.get("DOCKER_HOST", "").startswith("unix:///run/user/")
        command = [systemctl, "--user"] if rootless else [systemctl]
        try:
            service = _check(command[0], *command[1:], "show", "-p", "LoadState", "--value", "docker.service", retry=False)
        except (OSError, TimeoutExpired):
            return None
        if not service.returncode and service.stdout.strip() == "loaded":
            return [*command, "start", "docker.service"]
    return None


def _start_docker() -> str:
    command = _start_command()
    if command is None:
        url = _install_url()
        webbrowser.open(url)
        raise DockerSetupError(f"Complete Docker setup and retry. Official instructions: {url}")
    print("Starting the local Docker engine. Complete any system prompts.", file=sys.stderr, flush=True)
    try:
        started = Popen(command)
    except OSError as error:
        raise DockerSetupError(f"Could not start Docker. Start it from your system and retry. {_install_url()}") from error
    deadline = monotonic() + 60
    while monotonic() < deadline:
        if started.poll() not in (None, 0):
            raise DockerSetupError(f"Docker startup exited with status {started.returncode}. {_install_url()}")
        try:
            return check_linux_docker(retry=False)
        except DockerSetupError as error:
            if not error.startable:
                raise
        sleep(1)
    raise DockerSetupError("Docker did not become ready. Finish engine setup and retry.")


def source_workspace() -> Path | None:
    parents = Path(__file__).resolve().parents
    if not getattr(sys, "frozen", False) and len(parents) > 4:
        project = parents[3]
        if (project / "sandbox/Dockerfile").is_file() and (project / "pyproject.toml").is_file():
            return project
    return None


def image_available(executable: str, image: str) -> bool:
    try:
        return _check(executable, "image", "inspect", image).returncode == 0
    except (OSError, TimeoutExpired) as error:
        raise DockerSetupError(f"Could not inspect the sandbox image '{image}'.") from error


def prepare_docker(image: str, *, pull: bool, source: Path | None = None, start: bool = False) -> None:
    try:
        executable = check_linux_docker()
    except DockerSetupError as error:
        if not start or not error.startable:
            raise
        executable = _start_docker()
    if image_available(executable, image):
        return
    if pull:
        stage = "download"
        command = [executable, "pull", image]
        print(f"Downloading Docker workspace {image} …", file=sys.stderr, flush=True)
    elif source is not None:
        stage = "build"
        command = [executable, "build", "-f", str(source / "sandbox/Dockerfile"), "-t", image, str(source)]
        print(f"Building Docker workspace {image} …", file=sys.stderr, flush=True)
    else:
        raise DockerSetupError(
            f"Docker image '{image}' is missing. Install it or select a workspace image with --docker-image."
        )
    try:
        with Popen(
            command, stdin=DEVNULL, stdout=PIPE, stderr=STDOUT,
            encoding="utf-8", errors="replace",
        ) as process:
            try:
                for line in process.stdout:
                    print(line.rstrip(), file=sys.stderr, flush=True)
            except KeyboardInterrupt:
                process.terminate()
                raise
            if process.wait():
                raise DockerSetupError(f"Docker image {stage} failed for '{image}'. Check the output above and retry.")
    except OSError as error:
        raise DockerSetupError(f"Could not start the Docker image {stage} for '{image}'.") from error
