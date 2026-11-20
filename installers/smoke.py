"""Check an installed CLI and optionally run its sample through OpenRouter and Docker."""

import argparse
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


def smoke(executable: Path, *, docker: bool = False, image: str | None = None) -> None:
    with TemporaryDirectory(prefix="agent-cook-installed-") as directory:
        command = [str(executable), "-C", directory]
        help_text = subprocess.check_output([*command, "--help"], cwd=directory, encoding="utf-8")
        if "--input" not in help_text:
            raise ValueError("The installed CLI could not show its help.")
        models = subprocess.check_output([*command, "--list-models"], cwd=directory, encoding="utf-8")
        if "chat" not in json.loads(models):
            raise ValueError("The installed CLI could not read its model catalog.")
        result = subprocess.run(
            command, input="/example\n/exit\n", capture_output=True, cwd=directory,
            encoding="utf-8", check=True, timeout=30,
        )
        sales = list(Path(directory).glob(".agent-cook/examples/*/资料 样例/表格/销售 数据.csv"))
        if len(sales) != 1 or "Notebook" not in sales[0].read_text(encoding="utf-8"):
            raise ValueError(f"The installed CLI could not unpack the sample. {result.stderr}")
        if docker:
            verify_delivery(executable, Path(directory), sales[0], image)


def verify_delivery(executable: Path, directory: Path, sales: Path, image: str | None) -> None:
    selected = sales.parents[1]
    relative = sales.relative_to(selected.parent)
    original = sales.read_bytes()
    command = [str(executable), "exec", "-C", str(directory), "--input", str(selected)]
    if image:
        command.extend(("--docker-image", image))
    command.append(
        f"Read the copied {relative.as_posix()} and verify it contains Notebook. "
        "Replace the copy's contents with exactly 'Agent Cook acceptance OK' on one line. "
        "Use submit_results to deliver that file at its original workspace path."
    )
    environment = dict(os.environ)
    environment.pop("AGENT_COOK_SANDBOX", None)
    result = subprocess.run(command, cwd=directory, env=environment, encoding="utf-8", timeout=600)
    result.check_returncode()
    delivered, = directory.glob(".agent-cook/runs/*-results")
    if sales.read_bytes() != original:
        raise ValueError("The selected input was modified.")
    if (delivered / relative).read_text(encoding="utf-8").strip() != "Agent Cook acceptance OK":
        raise ValueError("The installed CLI did not deliver the processed copy.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--docker", action="store_true", help="Use the configured OpenRouter account for a Docker task")
    parser.add_argument("--docker-image", help="Test a locally built sandbox image")
    args = parser.parse_args()
    smoke(args.executable.resolve(strict=True), docker=args.docker, image=args.docker_image)
