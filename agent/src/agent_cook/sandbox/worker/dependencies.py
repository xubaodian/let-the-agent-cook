import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_cook.sandbox.dependencies import (
    NODE_PACKAGES, PYTHON_PACKAGES, WORKER_DEPENDENCIES, installer_environment, npm_install_args,
    package_specs, publish_packages, python_install_args,
)
from agent_cook.sandbox.limits import MAX_COMMAND_SECONDS
from agent_cook.sandbox.worker.process import capture_command
from agent_cook.sandbox.worker.workspace import Workspace


def install_dependencies(_workspace: Workspace, ecosystem: str, packages: list[str]) -> dict:
    specifications = package_specs(ecosystem, packages)
    if not WORKER_DEPENDENCIES.is_dir():
        raise ValueError("Update the Docker workspace image or E2B template to install packages.")
    with TemporaryDirectory(prefix=".install-", dir=WORKER_DEPENDENCIES) as path:
        staging = Path(path)
        binaries = Path("/usr/local/bin")
        if ecosystem == "python":
            argv = python_install_args(str(binaries / "uv"), Path(sys.executable), staging, specifications)
            name = PYTHON_PACKAGES
        else:
            npm = Path("/usr/local/lib/node_modules/npm/bin/npm-cli.js")
            argv = npm_install_args(binaries / "node", npm, staging, specifications)
            name = NODE_PACKAGES
        result = capture_command(argv, staging, MAX_COMMAND_SECONDS, env=installer_environment(staging, (binaries,)))
        if result["timed_out"]:
            raise ValueError(f"The package installation exceeded {MAX_COMMAND_SECONDS} seconds.")
        if result["exit_code"]:
            details = (result["stderr"] or result["stdout"]).strip()
            raise ValueError(f"Could not install {ecosystem} packages. {details[-450:]}")
        publish_packages(staging / name, WORKER_DEPENDENCIES / name)
    return {"installed": True}
