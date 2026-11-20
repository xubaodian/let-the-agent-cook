"""Check an installed CLI outside the checkout, including its packaged sample."""

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def smoke(executable: Path) -> None:
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


if __name__ == "__main__":
    smoke(Path(sys.argv[1]).resolve(strict=True))
