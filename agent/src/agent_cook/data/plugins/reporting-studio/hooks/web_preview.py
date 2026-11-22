import json
import sys
from hashlib import file_digest
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

SITE = PurePosixPath("out/site")
ENTRY = SITE / "index.html"
RECEIPT = Path("qa/web-preview.json")


def snapshot() -> dict[str, str]:
    files = {}
    for path in sorted(Path(SITE).rglob("*")):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                files[path.as_posix()] = file_digest(stream, "sha256").hexdigest()
    return files


def record_preview(payload: dict) -> None:
    arguments = payload["arguments"]
    if PurePosixPath(arguments.get("path", ".")) != ENTRY:
        served_site = (
            PurePosixPath(arguments.get("cwd", ".")) == SITE
            and arguments.get("server_argv")
            and urlsplit(arguments.get("url", "")).path in ("", "/", "/index.html")
        )
        if not served_site:
            return

    result = payload["result"]
    if result["is_error"]:
        RECEIPT.unlink(missing_ok=True)
        return
    observation = json.loads(result["model_content"])
    if observation["page_errors"]:
        RECEIPT.unlink(missing_ok=True)
        return
    if not any(action["action"] == "expect_text" for action in arguments.get("actions", ())):
        return
    files = snapshot()
    screenshot = observation["screenshot"]
    if str(ENTRY) not in files or screenshot not in files:
        return

    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    pending = RECEIPT.with_suffix(".tmp")
    pending.write_text(json.dumps({
        "tool_call_id": payload["tool_call_id"],
        "screenshot": screenshot,
        "files": files,
    }), encoding="utf-8")
    pending.replace(RECEIPT)


def check_submission(paths: list[str]) -> None:
    submitted = {PurePosixPath(path) for path in paths}
    if not any(path.is_relative_to(SITE) for path in submitted):
        return
    try:
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        checked = receipt["files"]
        screenshot = PurePosixPath(receipt["screenshot"])
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit("Run a passing Playwright check with a text assertion and a PNG under out/site/.") from None
    if checked != snapshot():
        raise SystemExit("The site changed after browser verification. Rerun Playwright and capture a fresh screenshot.")
    if ENTRY not in submitted or screenshot not in submitted:
        raise SystemExit(f"Submit {ENTRY} and the current screenshot {screenshot} with the site files.")


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    if payload["event"] == "after_tool":
        record_preview(payload)
    else:
        check_submission(payload["arguments"]["paths"])
