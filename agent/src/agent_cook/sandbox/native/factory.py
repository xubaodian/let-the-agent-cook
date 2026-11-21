import asyncio
import json
import os
import shutil
import sys
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agent_cook.sandbox.interface import CommandResult, SandboxError
from agent_cook.sandbox.limits import MAX_COMMAND_SECONDS, MAX_FILE_BYTES
from agent_cook.sandbox.native.executor import environment, execute
from agent_cook.sandbox.native.runtime import chromium_runtime, node_runtime, python_runtime
from agent_cook.sandbox.paths import portable_path
from agent_cook.sandbox.worker.workspace import Workspace, copy_file, list_files, read_file, search_files, write_file
from agent_cook.settings import DATA

_FILES = {
    "list_files": list_files, "search_files": search_files, "read_file": read_file,
    "write_file": write_file, "copy_file": copy_file,
}


class NativeSandbox:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        workspace = self.root / "workspace"
        self.scratch = self.root / "scratch"
        workspace.mkdir()
        self.scratch.mkdir()
        self.workspace = Workspace(workspace)
        self._lock = asyncio.Lock()
        self._python: tuple[Path, tuple[Path, ...]] | None = None
        self._node: Path | None = None

    async def put_file(self, path: str, content: bytes, *, overwrite: bool = False) -> None:
        if len(content) > MAX_FILE_BYTES:
            raise SandboxError("The input file exceeds the per-file transfer limit.")
        async with self._lock:
            try:
                self.workspace.write_bytes(path, content, overwrite=overwrite)
            except (OSError, ValueError) as error:
                raise SandboxError(str(error)[:400]) from error

    async def get_file(self, path: str) -> bytes:
        async with self._lock:
            try:
                return self.workspace.read_bytes(path)
            except (OSError, ValueError) as error:
                raise SandboxError(str(error)[:400]) from error

    async def _runtime(self, name: str) -> tuple[Path, tuple[Path, ...]]:
        binary = name.lower() if sys.platform == "win32" else name
        if binary in ("python", "python3", "python.exe", "python3.exe"):
            if self._python is None:
                self._python = await python_runtime()
            return self._python
        if binary in ("node", "node.exe"):
            if self._node is None:
                self._node = await asyncio.to_thread(node_runtime)
            return self._node, (self._node.parent,)
        if sys.platform == "win32":
            system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
            programs = {
                "cmd": system / "cmd.exe", "cmd.exe": system / "cmd.exe",
                "powershell": system / "WindowsPowerShell/v1.0/powershell.exe",
                "powershell.exe": system / "WindowsPowerShell/v1.0/powershell.exe",
            }
            if binary in programs and programs[binary].is_file():
                return programs[binary], ()
        elif "/" not in binary and (path := shutil.which(binary, path="/usr/bin:/bin")):
            return Path(path), ()
        if name.startswith("./"):
            executable = self.workspace.root / portable_path(name)
            if executable.is_file() and executable.resolve().is_relative_to(self.workspace.root):
                return executable, ()
        raise SandboxError(
            "Use python, node, a platform system command, or a workspace program prefixed with './'."
        )

    async def _execute(
        self, argv: Sequence[str], cwd: str, timeout: float, *, payload: bytes | None = None,
    ) -> CommandResult:
        if not argv or not argv[0] or not 0 < timeout <= MAX_COMMAND_SECONDS:
            raise SandboxError("Provide a program and a timeout within the command limit.")
        directory = self.workspace.directory_path(cwd)
        executable, reads = await self._runtime(argv[0])
        env = environment(self.scratch, (executable.parent,))
        return await execute(
            [str(executable), *argv[1:]], root=self.root, cwd=directory, reads=reads,
            env=env, timeout=timeout, payload=payload,
        )

    async def exec(
        self, argv: Sequence[str], *, cwd: str, timeout_seconds: float,
    ) -> CommandResult:
        async with self._lock:
            try:
                return await self._execute(argv, cwd, timeout_seconds)
            except (OSError, ValueError) as error:
                raise SandboxError(str(error)[:400]) from error

    async def _preview(self, arguments: dict[str, Any], timeout: float) -> dict[str, Any]:
        if "path" not in arguments or set(arguments) - {"path", "actions", "screenshot"}:
            raise SandboxError("Local Playwright takes a workspace HTML path. Use web_fetch for public webpages.")
        path = arguments["path"] = portable_path(arguments["path"])
        self.workspace.file_size(path)
        if shot := arguments.get("screenshot"):
            arguments["screenshot"] = portable_path(shot)
            if not shot.lower().endswith(".png"):
                raise SandboxError("Choose a .png screenshot path.")
        python, reads = await self._runtime("python")
        chromium = await chromium_runtime(python)
        program = DATA / "native/preview.py"
        env = environment(self.scratch, (python.parent,))
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(chromium)
        windows = sys.platform == "win32"
        if windows:
            # The controller launches the browser through the AppContainer helper.
            launcher = [sys.executable]
            if getattr(sys, "frozen", False):
                env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            else:
                launcher.extend(("-I", "-m", "agent_cook"))
            arguments["windows_launcher"] = launcher
            arguments["windows_scratch"] = str(self.scratch)
        result = await execute(
            [str(python), "-I", str(program)], root=self.root, cwd=self.workspace.root,
            reads=(*reads, chromium, program.parent), env=env, timeout=timeout, maximum=64 * 1024,
            payload=json.dumps(arguments, ensure_ascii=False).encode(), browser_controller=windows,
        )
        if result.timed_out or result.exit_code:
            output = result.stderr.strip()
            if len(output) > 1500:
                output = output[:700] + "\n…\n" + output[-750:]
            raise SandboxError(f"Local Chromium preview failed (exit {result.exit_code}). {output}")
        return json.loads(result.stdout)

    async def request(
        self, operation: str, arguments: dict[str, Any], *, timeout_seconds: float = 15,
    ) -> dict[str, Any]:
        async with self._lock:
            try:
                if tool := _FILES.get(operation):
                    return tool(self.workspace, **arguments)
                if operation == "run_hook":
                    return asdict(await self._execute(
                        ("python", "-I", "-c", arguments["source"]), ".", arguments["timeout_seconds"],
                        payload=json.dumps(arguments["payload"], ensure_ascii=False).encode(),
                    ))
                if operation == "playwright":
                    return await self._preview(dict(arguments), timeout_seconds)
            except (OSError, ValueError, TypeError, KeyError) as error:
                raise SandboxError(str(error)[:600]) from error
            raise SandboxError(f"The native workspace does not support {operation!r}.")

    async def probe(self, *, python: bool = False) -> None:
        if python:
            argv = ("python", "-I", "-c", "import openpyxl, pptx, playwright")
        else:
            argv = ("cmd", "/d", "/c", "exit", "0") if sys.platform == "win32" else ("true",)
        result = await self.exec(argv, cwd=".", timeout_seconds=15 if python else 5)
        if result.exit_code or result.timed_out:
            raise SandboxError(
                f"Native isolation probe failed (exit {result.exit_code}). {result.stderr.strip()[:400]}"
            )


class NativeSandboxFactory:
    @asynccontextmanager
    async def open(self, run_id: str):
        with TemporaryDirectory(prefix="agent-cook-") as path:
            yield NativeSandbox(Path(path))

    async def probe(self, *, python: bool = False) -> None:
        async with self.open("doctor") as sandbox:
            await sandbox.probe(python=python)
