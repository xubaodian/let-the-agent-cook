import argparse
import os
import shlex
import shutil
import sqlite3
import stat
import sys
import zipfile
from contextlib import closing
from pathlib import Path
from subprocess import DEVNULL, PIPE, CalledProcessError, TimeoutExpired, run
from tempfile import TemporaryFile

import httpx

from agent_cook import managed_launch
from agent_cook.local_files import PortablePaths
from agent_cook.managed_launch import installed_revisions, is_revision, source_command, version_lease
from agent_cook.sandbox.native.runtime import uv_executable
from agent_cook.sandbox.paths import is_link, portable_path
from agent_cook.settings import DATA, write_private_file


_REPOSITORY = "xubaodian/let-the-agent-cook"
_ARCHIVE_LIMIT = 128 * 1024 * 1024
_UV = "uv.exe" if sys.platform == "win32" else "uv"


def installation_directory() -> Path:
    home = Path.home()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or home / "AppData/Local") / "Agent Cook" / "cli"
    if sys.platform == "darwin":
        return home / "Library/Application Support/Agent Cook/cli"
    return Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share") / "agent-cook" / "cli"


def _latest_revision(client: httpx.Client) -> str:
    response = client.get(f"https://api.github.com/repos/{_REPOSITORY}/commits/main")
    response.raise_for_status()
    document = response.json()
    revision = document.get("sha") if isinstance(document, dict) else None
    if not isinstance(revision, str) or not is_revision(revision):
        raise ValueError("GitHub did not return a valid Agent Cook commit.")
    return revision


def _download(client: httpx.Client, revision: str, project: Path) -> None:
    prefix = f"let-the-agent-cook-{revision}/agent/"
    with TemporaryFile() as bundle:
        with client.stream("GET", f"https://codeload.github.com/{_REPOSITORY}/zip/{revision}") as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                if bundle.tell() + len(chunk) > _ARCHIVE_LIMIT:
                    raise ValueError("The Agent Cook download exceeds 128 MiB.")
                bundle.write(chunk)
        bundle.seek(0)
        with zipfile.ZipFile(bundle) as archive:
            paths = PortablePaths()
            size = 0
            for entry in archive.infolist():
                if entry.is_dir() or not entry.orig_filename.startswith(prefix):
                    continue
                relative = portable_path(entry.orig_filename.removeprefix(prefix))
                mode = entry.external_attr >> 16
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                    raise ValueError("The Agent Cook archive contains a special file.")
                if relative.split("/", 1)[0] in {".env", ".ready", ".venv", ".agent-cook"}:
                    raise ValueError("The Agent Cook archive contains a reserved installation path.")
                paths.add(relative)
                size += entry.file_size
                if size > _ARCHIVE_LIMIT:
                    raise ValueError("Agent Cook source files exceed 128 MiB.")
                target = project / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                if mode & stat.S_IXUSR and os.name == "posix":
                    target.chmod(0o755)
    required = ("cook", "cook.ps1", "uv.lock", "src/agent_cook/managed_launch.py")
    if not all((project / path).is_file() for path in required):
        raise ValueError("The selected GitHub commit does not contain a compatible Agent Cook installer.")


def _install_env() -> dict[str, str]:
    excluded = {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "OPENROUTER_API_KEY", "EXA_API_KEY", "E2B_API_KEY"}
    return {key: value for key, value in os.environ.items() if key not in excluded}


def _prepare(client: httpx.Client, root: Path, revision: str) -> Path:
    project = root / "versions" / revision
    if (project / ".ready").is_file():
        return project
    _prune(root, revision)
    project.mkdir(mode=0o700)
    try:
        _download(client, revision, project)
        print(f"Preparing Agent Cook {revision[:12]} …", file=sys.stderr, flush=True)
        run(source_command(project, "setup"), cwd=project, env=_install_env(), check=True, timeout=900)
        (project / ".ready").touch()
    except BaseException:
        shutil.rmtree(project, ignore_errors=True)
        raise
    return project


def _validate(project: Path, config: Path | None, models: Path | None) -> None:
    arguments = ["--list-models"]
    for flag, path in (("--config", config), ("--models-config", models)):
        if path is not None:
            arguments.extend((flag, str(path.expanduser().resolve())))
    probe = run(
        source_command(project, *arguments), env=_install_env(), stdin=DEVNULL,
        stdout=DEVNULL, stderr=PIPE, encoding="utf-8", errors="replace", timeout=60,
    )
    if probe.returncode:
        details = probe.stderr.strip().splitlines()
        reason = f" {details[-1][:500]}" if details else ""
        raise ValueError(f"The target version could not start with the current configuration.{reason}")


def _activate(root: Path, revision: str, previous: str | None) -> None:
    state = [revision] if previous is None else [revision, previous]
    write_private_file(root / "current", ("\n".join(state) + "\n").encode("ascii"))


def _prune(root: Path, revision: str, *, required: bool = True) -> None:
    project = root / "versions" / revision
    if not project.exists():
        return
    if is_link(project.lstat()) or not project.is_dir():
        raise ValueError(f"'{project}' must be a regular version directory.")
    try:
        with version_lease(root, revision, exclusive=True):
            shutil.rmtree(project)
    except (sqlite3.OperationalError, OSError) as error:
        if required:
            raise ValueError(
                f"Could not release version {revision[:12]}. Close consoles or services still using it and retry."
            ) from error


def _bootstrap_source(root: Path) -> None:
    # Preserve the existing project .env once, outside the version directories.
    source = Path(sys.prefix).parent
    shared_env = root / "versions/.env"
    if (source / "uv.lock").is_file() and not shared_env.exists():
        for original in (source / ".env", source.parent / ".env"):
            if original.is_file():
                write_private_file(shared_env, original.read_bytes())
                break
    if uv := uv_executable():
        shared_uv = root / "versions/.agent-cook/tools" / _UV
        if not shared_uv.exists():
            shared_uv.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(uv, shared_uv)


def _install_launcher(root: Path) -> None:
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    write_private_file(root / "python", (sys._base_executable + "\n").encode("utf-8"))
    write_private_file(root / "launcher.py", Path(managed_launch.__file__).read_bytes())
    names = ("agent-cook.cmd", "agent-cook.ps1") if sys.platform == "win32" else ("agent-cook",)
    for name in names:
        target = bin_dir / name
        write_private_file(target, (DATA / "launch" / name).read_bytes(), executable=True)


def _register_path(root: Path, revision: str) -> None:
    bin_dir = root / "bin"
    if sys.platform == "win32":
        launcher = str(bin_dir / "agent-cook.cmd").replace("'", "''")
        direct = f"& '{launcher}'"
    else:
        direct = shlex.quote(str(bin_dir / "agent-cook"))
    print(f"Start now with {direct}")
    versions = root / "versions"
    candidates = [project / ".agent-cook/tools" / _UV for project in (versions / revision, versions)]
    uv = next((str(path) for path in candidates if path.is_file()), None) or uv_executable()
    if uv:
        try:
            env = {**os.environ, "UV_TOOL_BIN_DIR": str(bin_dir)}
            run([uv, "tool", "update-shell"], env=env, check=True, timeout=30)
            print("Open a new terminal and run agent-cook.")
            return
        except (OSError, CalledProcessError, TimeoutExpired):
            pass
    print(f"Add '{bin_dir}' to your user PATH to use agent-cook.")


def manage_source_install(
    *, install: bool = False, check: bool = False, rollback: bool = False,
    config: Path | None = None, models: Path | None = None,
) -> None:
    if getattr(sys, "frozen", False):
        raise ValueError(f"Update this native installation from https://github.com/{_REPOSITORY}/releases/latest.")
    root = installation_directory().resolve()
    current, previous = installed_revisions(root)
    if current is None and not install:
        installer = r".\install.cmd --global" if sys.platform == "win32" else "./install --global"
        raise ValueError(f"Set up the managed source installation with {installer} from the agent directory.")
    try:
        with httpx.Client(timeout=httpx.Timeout(60, connect=10)) as client:
            if check:
                latest = _latest_revision(client)
                status = "Already up to date." if latest == current else "Update available."
                print(f"{status} Installed {current[:12]}. GitHub main {latest[:12]}.")
                return
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            versions = root / "versions"
            versions.mkdir(exist_ok=True)
            with closing(sqlite3.connect(root / "update.sqlite3", timeout=0)) as lease:
                lease.execute("BEGIN IMMEDIATE")
                current, previous = installed_revisions(root)
                if rollback:
                    if previous is None or not (versions / previous / ".ready").is_file():
                        raise ValueError("There is no previous Agent Cook version to restore.")
                    _validate(versions / previous, config, models)
                    _activate(root, previous, current)
                    print(f"Restored {previous[:12]}. The next CLI launch will use it.")
                    return
                latest = _latest_revision(client)
                if install:
                    _bootstrap_source(root)
                retained = {current, previous, latest}
                for project in versions.iterdir():
                    if is_revision(project.name) and project.name not in retained:
                        _prune(root, project.name, required=latest != current)
                if latest != current or not (versions / latest / ".ready").is_file():
                    project = _prepare(client, root, latest)
                    _validate(project, config, models)
                elif install:
                    run(
                        source_command(versions / latest, "setup-runtime"),
                        env=_install_env(), check=True, timeout=900,
                    )
                if install:
                    _install_launcher(root)
                if latest != current:
                    _activate(root, latest, current)
                    if previous is not None and previous != latest:
                        _prune(root, previous, required=False)
                    print(f"Installed Agent Cook {latest[:12]}. The next CLI launch will use it.")
                else:
                    print(f"Agent Cook {latest[:12]} is already up to date.")
    except httpx.HTTPError as error:
        raise ValueError(f"Could not download Agent Cook from GitHub ({error}).") from None
    except zipfile.BadZipFile:
        raise ValueError("GitHub returned an invalid Agent Cook archive.") from None
    except sqlite3.OperationalError as error:
        if "locked" in str(error):
            raise ValueError("Another Agent Cook update is in progress. Try again after it finishes.") from None
        raise
    except (CalledProcessError, TimeoutExpired):
        raise ValueError("Agent Cook could not prepare the target version. Review the installation output.") from None
    if install:
        _register_path(root, latest)


def update_command(command: str, words: list[str]) -> int:
    installing = command == "install-global"
    parser = argparse.ArgumentParser(prog=f"agent-cook {command}")
    parser.add_argument("--config", type=Path, help="Validate this configuration with the target version")
    parser.add_argument("--models-config", type=Path, help="Validate this model catalog with the target version")
    if installing:
        parser.add_argument("--with-office", action="store_true", help=argparse.SUPPRESS)
    else:
        modes = parser.add_mutually_exclusive_group()
        modes.add_argument("--check", action="store_true", help="Check GitHub main without changing the installation")
        modes.add_argument("--rollback", action="store_true", help="Switch back to the previous installed version")
    args = parser.parse_args(words)
    try:
        manage_source_install(
            install=installing, check=getattr(args, "check", False), rollback=getattr(args, "rollback", False),
            config=args.config, models=args.models_config,
        )
    except KeyboardInterrupt:
        print("Update cancelled.", file=sys.stderr)
        return 130
    except (OSError, ValueError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0
