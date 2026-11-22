import json
import os
import re
import stat
from pathlib import Path

from agent_cook.sandbox.paths import is_link

PACKAGE_BATCH_LIMIT = 24
PACKAGE_SPEC_LENGTH = 160
WORKER_DEPENDENCIES = Path("/opt/agent-cook/task-dependencies")
PYTHON_PACKAGES = "python-packages"
NODE_PACKAGES = "node_modules"

_PYTHON = re.compile(r"([a-zA-Z0-9](?:[a-zA-Z0-9_.-]*[a-zA-Z0-9])?)(?:==[a-zA-Z0-9][a-zA-Z0-9.+!_-]*)?")
_NPM_NAME = r"[a-z0-9][a-z0-9_.-]*"
_NODE = re.compile(rf"((?:@{_NPM_NAME}/)?{_NPM_NAME})(?:@(\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?(?:\+[a-zA-Z0-9.-]+)?))?")


def package_specs(ecosystem: str, packages: list[str]) -> dict[str, str]:
    if ecosystem not in ("python", "node") or not isinstance(packages, list) or not packages:
        raise ValueError("Choose python or node and provide a list of package specifications.")
    result = {}
    for package in packages:
        if not isinstance(package, str) or len(package) > PACKAGE_SPEC_LENGTH:
            raise ValueError("Provide a package name with an optional exact version.")
        if ecosystem == "python":
            match = _PYTHON.fullmatch(package)
            if match is None:
                raise ValueError("Use PyPI names such as humanize or humanize==4.13.0.")
            name = re.sub(r"[-_.]+", "-", match[1]).lower()
            result[name] = package
        else:
            match = _NODE.fullmatch(package)
            if match is None:
                raise ValueError("Use npm names such as kleur@4.1.5 or @scope/package.")
            result[match[1]] = match[2] or "*"
    return result


def installer_environment(staging: Path, paths: tuple[Path, ...]) -> dict[str, str]:
    # Managers start in a fresh directory and use neither the task nor the user's configuration.
    environment = {
        "HOME": str(staging), "USERPROFILE": str(staging), "TMP": str(staging), "TEMP": str(staging),
        "TMPDIR": str(staging), "APPDATA": str(staging), "LOCALAPPDATA": str(staging),
        "XDG_CONFIG_HOME": str(staging), "XDG_CACHE_HOME": str(staging), "LANG": "C.UTF-8",
    }
    if os.name == "nt":
        system = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        environment.update(SystemRoot=str(system), WINDIR=str(system))
        paths += (system / "System32",)
    environment["PATH"] = os.pathsep.join(map(str, paths))
    return environment


def python_install_args(uv: str, python: Path, staging: Path, packages: dict[str, str]) -> list[str]:
    requirements = staging / "requirements.txt"
    requirements.write_text("\n".join(packages.values()) + "\n", encoding="utf-8")
    return [
        uv, "--no-config", "--no-cache", "--no-progress", "--no-python-downloads", "pip", "install",
        "--only-binary", ":all:", "--link-mode", "copy", "--default-index", "https://pypi.org/simple",
        "--python", str(python), "--target", str(staging / PYTHON_PACKAGES), "-r", str(requirements),
    ]


def npm_install_args(node: Path, npm: Path, staging: Path, packages: dict[str, str]) -> list[str]:
    (staging / "package.json").write_text(
        json.dumps({"name": "agent-cook-task", "private": True, "dependencies": packages}), encoding="utf-8",
    )
    user_config = staging / "user.npmrc"
    global_config = staging / "global.npmrc"
    user_config.touch()
    global_config.touch()
    return [
        str(node), str(npm), "install", "--prefix", str(staging),
        "--userconfig", str(user_config), "--globalconfig", str(global_config), "--cache", str(staging / "cache"),
        "--registry", "https://registry.npmjs.org/", "--ignore-scripts",
        "--allow-git=none", "--allow-remote=none", "--allow-directory=none", "--allow-file=none",
        "--no-audit", "--no-fund", "--no-bin-links", "--fetch-retries=1", "--loglevel=error",
    ]


def publish_packages(staged: Path, destination: Path) -> None:
    # Publish only a complete install. Keep the previous directory if installation or the swap fails.
    if not staged.is_dir():
        raise ValueError("The package manager did not produce an installation.")
    previous = staged.parent / "previous-packages"
    try:
        info = destination.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"The task dependency path '{destination.name}' must be a regular directory.")
        destination.rename(previous)
    try:
        staged.rename(destination)
    except OSError:
        if info is not None:
            previous.rename(destination)
        raise
