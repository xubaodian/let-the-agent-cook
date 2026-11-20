import json
import sys
from pathlib import Path, PurePosixPath

REPORT = PurePosixPath("out/report")
VALIDATION = REPORT / "validation.json"


def check_submission(paths: list[str]) -> None:
    submitted = {PurePosixPath(path) for path in paths}
    if not any(path.is_relative_to(REPORT) for path in submitted):
        return
    try:
        validation = json.loads(Path(VALIDATION).read_text(encoding="utf-8"))
        declared = validation["files"]
        checks = validation["checks"]
        if not isinstance(declared, list) or not declared or not isinstance(checks, dict) or not checks:
            raise ValueError
        expected = {PurePosixPath(path) for path in declared}
        if any(path == REPORT or not path.is_relative_to(REPORT) or ".." in path.parts for path in expected):
            raise ValueError
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        raise SystemExit(f"Record the report deliverables and check results in {VALIDATION}.") from None

    failed = sorted(name for name, passed in checks.items() if passed is not True)
    if failed:
        raise SystemExit(f"Resolve these report checks before submitting: {', '.join(failed)}.")
    missing = sorted(str(path) for path in expected if path not in submitted or not Path(path).is_file())
    if missing:
        raise SystemExit(f"Submit the complete report batch, including: {', '.join(missing)}.")


if __name__ == "__main__":
    check_submission(json.load(sys.stdin)["arguments"]["paths"])
