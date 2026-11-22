"""Check an installed CLI and optionally run its sample through OpenRouter."""

import argparse
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


def smoke(executable: Path, *, files: bool = False) -> None:
    with TemporaryDirectory(prefix="agent-cook-installed-") as directory:
        config = Path(directory) / "agent-cook.json"
        config.write_text(json.dumps({
            "api_keys": {"openrouter": "installed-key-fixture", "exa": "installed-exa-fixture"},
            "models": {"chat": {"available": ["installed/test"], "default": "installed/test"}},
        }), encoding="utf-8")
        command = [str(executable), "-C", directory, "--config", str(config)]
        workspace = Path(directory) / "workspace"
        environment = {
            **os.environ, "OPENROUTER_API_KEY": "", "EXA_API_KEY": "", "OPENROUTER_MODEL": "",
            "AGENT_COOK_WORKSPACE": str(workspace),
        }
        help_text = subprocess.check_output([*command, "--help"], cwd=directory, encoding="utf-8")
        if "--input" not in help_text:
            raise ValueError("The installed CLI could not show its help.")
        models = subprocess.check_output([*command, "--list-models"], cwd=directory, encoding="utf-8")
        if json.loads(models)["chat"]["default"] != "installed/test":
            raise ValueError("The installed CLI could not read its model catalog.")
        plugins = subprocess.check_output([*command, "--list-plugins"], cwd=directory, encoding="utf-8")
        if {entry["name"] for entry in json.loads(plugins)} != {"reporting-studio", "project-reporting-studio"}:
            raise ValueError("The installed CLI could not load its bundled studios.")
        result = subprocess.run(
            command, input="/config\n/example\n/exit\n", capture_output=True, cwd=directory, env=environment,
            encoding="utf-8", check=True, timeout=30,
        )
        if "Current model  installed/test" not in result.stdout or result.stdout.count("configured via agent-cook.json") != 2:
            raise ValueError("The installed CLI could not use the shared configuration.")
        if "installed-key-fixture" in result.stdout or "installed-exa-fixture" in result.stdout:
            raise ValueError("The installed CLI displayed a key.")
        sales = list(workspace.glob("examples/*/资料 样例/表格/销售 数据.csv"))
        if len(sales) != 1 or "Notebook" not in sales[0].read_text(encoding="utf-8"):
            raise ValueError(f"The installed CLI could not unpack the sample. {result.stderr}")
        doctor = subprocess.run(
            [str(executable), "doctor", "--runtime", *command[1:]], cwd=directory, env=environment,
            capture_output=True, encoding="utf-8", timeout=240,
        )
        if doctor.returncode or "OK  Native process isolation" not in doctor.stdout:
            raise ValueError(f"The installed CLI could not run in native isolation.\n{doctor.stdout}{doctor.stderr}")
        if files:
            verify_delivery(executable, Path(directory), sales[0], workspace)


def verify_delivery(executable: Path, directory: Path, sales: Path, workspace: Path) -> None:
    selected = sales.parents[1]
    relative = sales.relative_to(selected.parent)
    original = sales.read_bytes()
    command = [str(executable), "exec", "-C", str(directory), "--input", str(selected)]
    command.append(
        f"Read the copied {relative.as_posix()} and verify it contains Notebook. "
        "Replace the copy's contents with exactly 'Agent Cook acceptance OK' on one line. "
        "Use submit_results to deliver that file at its original workspace path."
    )
    environment = {**os.environ, "AGENT_COOK_WORKSPACE": str(workspace)}
    environment.pop("AGENT_COOK_SANDBOX", None)
    result = subprocess.run(command, cwd=directory, env=environment, encoding="utf-8", timeout=600)
    result.check_returncode()
    delivered, = workspace.glob("*/*/*-results")
    if sales.read_bytes() != original:
        raise ValueError("The selected input was modified.")
    if (delivered / relative).read_text(encoding="utf-8").strip() != "Agent Cook acceptance OK":
        raise ValueError("The installed CLI did not deliver the processed copy.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--files", action="store_true", help="Use the configured OpenRouter account for a file task")
    args = parser.parse_args()
    smoke(args.executable.resolve(strict=True), files=args.files)
