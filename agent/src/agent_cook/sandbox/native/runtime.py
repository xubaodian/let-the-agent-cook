import asyncio
import hashlib
import os
import platform
import shutil
import sys
import tarfile
import zipfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile
from urllib.request import urlopen

from agent_cook.sandbox.interface import SandboxError
from agent_cook.sandbox.native.office import prepare_office
from agent_cook.settings import DATA

_NODE_VERSION = "24.22.3"
# Published checksums from the Node.js release's SHASUMS256.txt.
_NODE_ARCHIVES = {
    ("darwin", "arm64"): ("darwin-arm64.tar.xz", "1d64136c8a43d648d1b3bad10797a4c995b1ca032d2bde8799de43b37f058fe5"),
    ("darwin", "x64"): ("darwin-x64.tar.xz", "4daeec962855c1a6e66cda398a25037319dc1bbd4a0939188b3547afbbfb6490"),
    ("linux", "arm64"): ("linux-arm64.tar.xz", "6e51a2ada00f0011cc266f65d98922b403d7ad88f809ca2aa615a5fe1ce6e86c"),
    ("linux", "x64"): ("linux-x64.tar.xz", "4f2b175a6d53a5c617a80dba2676238a0688cef28c6da4eba35c7d844e66fcfb"),
    ("win32", "x64"): ("win-x64.zip", "e612af99f626ab415ea225af6bce5c971feb448cb5cae250bb1c3fa50c5f7d9f"),
}
_REQUIREMENTS = DATA / "native/workspace-requirements.txt"


def cache_directory() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library/Caches/Agent Cook"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or home / "AppData/Local") / "Agent Cook" / "cache"
    return Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache") / "agent-cook"


def uv_executable() -> str | None:
    name = "uv.exe" if sys.platform == "win32" else "uv"
    configured = os.environ.get("AGENT_COOK_UV")
    source = Path(__file__).resolve().parents[4]
    candidates = (Path(configured) if configured else None, DATA / "native" / name)
    if (source / "uv.lock").is_file():
        candidates += tuple(project / ".agent-cook/tools" / name for project in (source, source.parent))
    for path in candidates:
        if path is not None and path.is_file():
            return str(path.resolve())
    return shutil.which(name)


async def run_installer(
    program: str, *args: str, cwd: Path | None = None, env: dict[str, str] | None = None,
    timeout: float = 300,
) -> None:
    with TemporaryFile() as output:
        try:
            process = await asyncio.create_subprocess_exec(
                program, *args, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=output, stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as error:
            raise SandboxError("Could not start the workspace installer.") from error
        try:
            async with asyncio.timeout(timeout):
                await process.wait()
        except TimeoutError as error:
            raise SandboxError(f"The workspace installer exceeded {timeout:g} seconds.") from error
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        if process.returncode:
            output.seek(max(0, output.tell() - 4096))
            details = output.read().decode("utf-8", errors="replace").strip().splitlines()
            raise SandboxError("Workspace preparation failed. " + " ".join(details[-4:])[:500])


def _python_version() -> str:
    for path in (DATA / "native/.python-version", Path(__file__).resolve().parents[4] / ".python-version"):
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    return "3.12"


def _installed_profile(requirements: bytes) -> bool:
    if getattr(sys, "frozen", False):
        return False
    try:
        for line in requirements.decode("utf-8").splitlines():
            if line and not line.startswith("#"):
                name, pin = line.split("==", 1)
                if version(name) != pin:
                    return False
    except PackageNotFoundError:
        return False
    return True


def _python_roots(executable: Path) -> tuple[Path, ...]:
    prefix = executable.parent.parent
    if executable == Path(sys.executable).absolute() and not getattr(sys, "frozen", False):
        prefix, base = Path(sys.prefix), Path(sys.base_prefix)
    else:
        config = {}
        for line in (prefix / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
        base = Path(config["home"])
        if base.name.lower() in ("bin", "scripts"):
            base = base.parent
    roots = (prefix, prefix.resolve(), base, base.resolve())
    # A managed venv can point through a version alias on its way to the actual binary.
    if os.name == "posix":
        roots += (executable.resolve(strict=True).parent.parent,)
    return tuple(dict.fromkeys(roots))


async def python_runtime() -> tuple[Path, tuple[Path, ...]]:
    requirements = _REQUIREMENTS.read_bytes()
    python_version = _python_version()
    if python_version == f"{sys.version_info.major}.{sys.version_info.minor}" and _installed_profile(requirements):
        executable = Path(sys.executable).absolute()
        return executable, _python_roots(executable)
    uv = uv_executable()
    if uv is None:
        raise SandboxError("uv is needed to prepare the task Python. Re-run the Agent Cook installer.")
    stamp = hashlib.sha256(python_version.encode("ascii") + b"\n" + requirements).hexdigest()[:16]
    directory = (cache_directory() / "python" / stamp).resolve()
    relative = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    executable = directory / relative
    if not executable.is_file():
        directory.parent.mkdir(parents=True, exist_ok=True)
        print(f"Preparing Python {python_version} workspace packages …", file=sys.stderr, flush=True)
        with TemporaryDirectory(prefix=".python-", dir=directory.parent) as staging:
            target = Path(staging) / "environment"
            await run_installer(
                uv, "--no-config", "venv", "--python", python_version, "--managed-python",
                "--relocatable", str(target), cwd=target.parent,
            )
            await run_installer(
                uv, "--no-config", "pip", "install", "--only-binary", ":all:", "--link-mode", "copy",
                "--python", str(target / relative), "-r", str(_REQUIREMENTS), cwd=target.parent,
            )
            try:
                target.rename(directory)
            except OSError:
                if not executable.is_file():
                    raise
    return executable, _python_roots(executable)


def npm_cli(node: Path) -> Path:
    prefix = node.parent if sys.platform == "win32" else node.parent.parent / "lib"
    return prefix / "node_modules/npm/bin/npm-cli.js"


def node_runtime() -> Path:
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(machine, machine)
    if (sys.platform, arch) not in _NODE_ARCHIVES:
        raise SandboxError(f"Agent Cook has no managed Node binary for {sys.platform}/{arch}.")
    suffix, expected = _NODE_ARCHIVES[sys.platform, arch]
    name = f"node-v{_NODE_VERSION}-{suffix}"
    base = name.removesuffix(".tar.xz").removesuffix(".zip")
    directory = (cache_directory() / "node" / base).resolve()
    relative = "node.exe" if sys.platform == "win32" else "bin/node"
    executable = directory / relative
    npm = npm_cli(executable)
    if executable.is_file() and npm.is_file():
        return executable
    directory.parent.mkdir(parents=True, exist_ok=True)
    print(f"Preparing Node.js {_NODE_VERSION} and npm …", file=sys.stderr, flush=True)
    try:
        with TemporaryDirectory(prefix=".node-", dir=directory.parent) as staging:
            download = Path(staging) / name
            with urlopen(f"https://nodejs.org/dist/v{_NODE_VERSION}/{name}", timeout=20) as source, download.open("xb") as out:
                shutil.copyfileobj(source, out)
            with download.open("rb") as contents:
                if hashlib.file_digest(contents, "sha256").hexdigest() != expected:
                    raise SandboxError("The Node.js download failed its checksum verification.")
            binary_entry = f"{base}/{relative}"
            npm_prefix = f"{base}/{npm.relative_to(directory).parents[1].as_posix()}/"
            if suffix.endswith(".zip"):
                with zipfile.ZipFile(download) as archive:
                    names = (
                        entry for entry in archive.namelist() if entry == binary_entry or entry.startswith(npm_prefix)
                    )
                    archive.extractall(staging, names)
            else:
                with tarfile.open(download) as archive:
                    members = (
                        entry for entry in archive if entry.name == binary_entry or entry.name.startswith(npm_prefix)
                    )
                    archive.extractall(staging, members, filter="data")
            candidate = Path(staging) / base
            if not (candidate / relative).is_file() or not (candidate / npm.relative_to(directory)).is_file():
                raise SandboxError("The Node.js archive is missing Node.js or npm.")
            try:
                candidate.rename(directory)
            except OSError:
                if not executable.is_file() or not npm.is_file():
                    raise
    except (OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as error:
        raise SandboxError("Could not prepare Node.js. Verify network access to nodejs.org and retry.") from error
    return executable


async def chromium_runtime(python: Path) -> Path:
    directory = cache_directory() / "chromium"
    version = directory / ".playwright-1.62.0"
    if not version.is_file():
        environment = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(directory)}
        print("Preparing Chromium for workspace previews …", file=sys.stderr, flush=True)
        await run_installer(str(python), "-I", "-m", "playwright", "install", "chromium", env=environment)
        version.touch()
    return directory


async def prepare_runtime() -> None:
    python, _ = await python_runtime()
    await asyncio.to_thread(node_runtime)
    await chromium_runtime(python)
    await asyncio.to_thread(prepare_office)
