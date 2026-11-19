import json
import os
import selectors
import signal
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile
from time import monotonic
from typing import Any

from agent_cook.sandbox.limits import MAX_COMMAND_OUTPUT, MAX_COMMAND_SECONDS
from agent_cook.sandbox.worker.workspace import Workspace

_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "PLAYWRIGHT_BROWSERS_PATH")


def launch(
    argv: list[str], cwd: Path, *, capture: bool, input_text: str | None = None,
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
    with ExitStack() as resources:
        stdin = subprocess.DEVNULL
        if input_text is not None:
            stdin = resources.enter_context(TemporaryFile())
            stdin.write(input_text.encode("utf-8"))
            stdin.seek(0)
        return subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=stdin,
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
    workspace: Workspace, argv: list[str], cwd: str = ".", timeout_seconds: float = 30,
    *, input_text: str | None = None,
) -> dict[str, Any]:
    if not 0 < timeout_seconds <= MAX_COMMAND_SECONDS:
        raise ValueError(f"Command timeout must be between 0 and {MAX_COMMAND_SECONDS} seconds.")
    process = launch(argv, workspace.directory_path(cwd), capture=True, input_text=input_text)
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


def run_hook(
    workspace: Workspace, source: str, payload: dict[str, Any], timeout_seconds: float,
) -> dict[str, Any]:
    if not isinstance(source, str) or not isinstance(payload, dict):
        raise ValueError("Provide Python source and a JSON hook payload.")
    with TemporaryDirectory() as directory:
        script = Path(directory) / "hook.py"
        script.write_text(source, encoding="utf-8")
        return run_command(
            workspace, [sys.executable, "-I", str(script)],
            timeout_seconds=timeout_seconds, input_text=json.dumps(payload, ensure_ascii=False),
        )
