import asyncio
import hashlib
import os
import platform
import shutil
import sys
import tarfile
import zipfile
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from agent_cook.sandbox.interface import SandboxError
from agent_cook.settings import DATA

_NODE_VERSION = "22.23.5"
# Published checksums from the Node.js release's SHASUMS256.txt.
_NODE_ARCHIVES = {
    ("darwin", "arm64"): ("darwin-arm64.tar.xz", "d0c68c321161bb2c6483efea9a343010dea8e5e7f60d73c6a7eba140016d662f"),
    ("darwin", "x64"): ("darwin-x64.tar.xz", "d68bf234a07781141dfca61ae4461c24560f70bd53223d76a41529eb02e8554e"),
    ("linux", "arm64"): ("linux-arm64.tar.xz", "2babb9eb5264860d540849f4b064a6fbdebcdc323657d9d382ce23d313fd5e09"),
    ("linux", "x64"): ("linux-x64.tar.xz", "cdeb8670a0c81f51b4dc4ac61919f9b1ad917b304bf8221eb939570663d240f8"),
    ("win32", "x64"): ("win-x64.zip", "4a191082e9706866830379d577d2cc8a50c776fcd9436a8d061b063ed0d9a0e3"),
}
_EXAMPLES = ("openpyxl", "pptx", "playwright")


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
    for path in (Path(configured) if configured else None, DATA / "native" / name):
        if path is not None and path.is_file():
            return str(path)
    return shutil.which(name)


async def _install(program: str, *args: str, env: dict[str, str] | None = None) -> None:
    # Install from a trusted, pinned manifest. Agent commands do not call this path.
    try:
        process = await asyncio.create_subprocess_exec(
            program, *args, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as error:
        raise SandboxError("Could not start the native runtime installer.") from error
    try:
        output, _ = await process.communicate()
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode:
        details = output.decode("utf-8", errors="replace").strip().splitlines()
        raise SandboxError("Native runtime preparation failed. " + " ".join(details[-4:])[:500])


def _project() -> Path:
    bundled = DATA / "native/project"
    if (bundled / "uv.lock").is_file():
        return bundled
    parents = Path(__file__).resolve().parents
    if len(parents) > 4 and (parents[4] / "uv.lock").is_file():
        return parents[4]
    raise SandboxError("Install Agent Cook from the source checkout or a native release to prepare the Python runtime.")


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
    if not getattr(sys, "frozen", False) and all(find_spec(package) for package in _EXAMPLES):
        executable = Path(sys.executable).absolute()
        return executable, _python_roots(executable)
    uv = uv_executable()
    if uv is None:
        raise SandboxError("uv is needed to prepare the task Python. Re-run the Agent Cook installer.")
    manifest = _project()
    version = (manifest / ".python-version").read_text(encoding="utf-8").strip()
    with (manifest / "uv.lock").open("rb") as lock:
        stamp = hashlib.file_digest(lock, "sha256").hexdigest()[:16]
    directory = (cache_directory() / "python" / stamp).resolve()
    executable = directory / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    marker = directory / ".installed"
    if not marker.is_file() or not executable.is_file():
        directory.parent.mkdir(parents=True, exist_ok=True)
        environment = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(directory)}
        await _install(
            uv, "sync", "--project", str(manifest), "--locked", "--no-install-project", "--extra", "examples",
            "--python", version, "--managed-python", env=environment,
        )
        marker.touch()
    return executable, _python_roots(executable)


def node_runtime() -> Path:
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(machine, machine)
    if (sys.platform, arch) not in _NODE_ARCHIVES:
        raise SandboxError(f"Agent Cook has no managed Node binary for {sys.platform}/{arch}.")
    suffix, expected = _NODE_ARCHIVES[sys.platform, arch]
    name = f"node-v{_NODE_VERSION}-{suffix}"
    directory = cache_directory() / "node" / name.removesuffix(".tar.xz").removesuffix(".zip")
    executable = directory / ("node.exe" if sys.platform == "win32" else "node")
    if executable.is_file():
        return executable
    directory.mkdir(parents=True, exist_ok=True)
    print(f"Preparing Node.js {_NODE_VERSION} for Agent Cook …", file=sys.stderr, flush=True)
    try:
        with TemporaryDirectory(prefix=".install-", dir=directory) as staging:
            download = Path(staging) / name
            with urlopen(f"https://nodejs.org/dist/v{_NODE_VERSION}/{name}", timeout=20) as source, download.open("xb") as out:
                shutil.copyfileobj(source, out)
            with download.open("rb") as contents:
                if hashlib.file_digest(contents, "sha256").hexdigest() != expected:
                    raise SandboxError("The Node.js download failed its checksum verification.")
            binary = Path(staging) / executable.name
            if suffix.endswith(".zip"):
                member = f"{name.removesuffix('.zip')}/node.exe"
                with zipfile.ZipFile(download) as archive, archive.open(member) as source, binary.open("xb") as out:
                    shutil.copyfileobj(source, out)
            else:
                member = f"{name.removesuffix('.tar.xz')}/bin/node"
                with tarfile.open(download) as archive, binary.open("xb") as out:
                    entry = archive.getmember(member)
                    if not entry.isfile():
                        raise SandboxError("The Node.js archive does not contain the expected executable.")
                    with archive.extractfile(entry) as source:
                        shutil.copyfileobj(source, out)
                binary.chmod(0o700)
            binary.replace(executable)
    except (OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as error:
        raise SandboxError("Could not prepare Node.js. Verify network access to nodejs.org and retry.") from error
    return executable


async def chromium_runtime(python: Path) -> Path:
    directory = cache_directory() / "chromium"
    version = directory / ".playwright-1.62.0"
    if not version.is_file():
        environment = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(directory)}
        await _install(str(python), "-I", "-m", "playwright", "install", "chromium", env=environment)
        version.touch()
    return directory
