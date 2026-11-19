import os
import selectors
import signal
import subprocess
from pathlib import Path
from time import monotonic
from typing import Any

from agent_cook.sandbox.limits import MAX_COMMAND_OUTPUT, MAX_COMMAND_SECONDS
from agent_cook.sandbox.worker.workspace import Workspace

_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "PLAYWRIGHT_BROWSERS_PATH")


def launch(
    argv: list[str], cwd: Path, *, capture: bool
) -> subprocess.Popen[bytes]:
    if (
        not isinstance(argv, list)
        or not 1 <= len(argv) <= 64
        or any(not isinstance(arg, str) or "\0" in arg or len(arg) > 16_000 for arg in argv)
        or not argv[0]
    ):
        raise ValueError("Pass a nonempty argv array with a program and its arguments.")
    target = subprocess.PIPE if capture else subprocess.DEVNULL
    env = {name: os.environ[name] for name in _ENV if name in os.environ}
    return subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=target,
        stderr=target,
        start_new_session=True,
    )


def stop(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def run_command(
    workspace: Workspace, argv: list[str], cwd: str = ".", timeout_seconds: float = 30
) -> dict[str, Any]:
    if not 0 < timeout_seconds <= MAX_COMMAND_SECONDS:
        raise ValueError(f"Command timeout must be between 0 and {MAX_COMMAND_SECONDS} seconds.")
    process = launch(argv, workspace.directory_path(cwd), capture=True)
    assert process.stdout is not None and process.stderr is not None
    output = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = monotonic() + timeout_seconds
    timed_out = False
    truncated = False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout_seconds)
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    buffer = output[key.data]
                    room = MAX_COMMAND_OUTPUT - len(buffer)
                    buffer.extend(chunk[:room])
                    truncated = truncated or len(chunk) > room
        process.wait(timeout=max(0, deadline - monotonic()))
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        try:
            stop(process)
        finally:
            process.stdout.close()
            process.stderr.close()
    return {
        "exit_code": process.returncode,
        "stdout": output["stdout"].decode("utf-8", errors="replace"),
        "stderr": output["stderr"].decode("utf-8", errors="replace"),
        "timed_out": timed_out,
        "truncated": truncated,
    }
