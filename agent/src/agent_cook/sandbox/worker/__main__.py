import argparse
import json
import sys
from pathlib import Path
from typing import Any, BinaryIO

from agent_cook.sandbox.limits import MAX_RPC_BYTES, WORKER_VERSION, WORKSPACE_ROOT
from agent_cook.sandbox.worker.browser import playwright_preview, playwright_public
from agent_cook.sandbox.worker.dependencies import install_dependencies
from agent_cook.sandbox.worker.process import run_command, run_hook
from agent_cook.sandbox.worker.workspace import (
    Workspace,
    copy_file,
    download,
    list_files,
    read_file,
    search_files,
    upload,
    write_file,
)

_OPERATIONS = {
    "copy_file": copy_file,
    "download": download,
    "install_dependencies": install_dependencies,
    "list_files": list_files,
    "playwright": playwright_preview,
    "playwright_public": playwright_public,
    "read_file": read_file,
    "run_command": run_command,
    "run_hook": run_hook,
    "search_files": search_files,
    "upload": upload,
    "write_file": write_file,
}


def _execute(root: Path, stream: BinaryIO) -> dict[str, Any]:
    payload = stream.read(MAX_RPC_BYTES + 1)
    if len(payload) > MAX_RPC_BYTES:
        raise ValueError("The request exceeds the workspace transfer limit.")
    request = json.loads(payload)
    if not isinstance(request, dict) or request.get("version") != WORKER_VERSION:
        raise ValueError("The workspace client and worker versions do not match.")
    operation = request.get("operation")
    arguments = request.get("arguments")
    if not isinstance(operation, str) or operation not in _OPERATIONS or not isinstance(arguments, dict):
        raise ValueError("Unknown workspace operation or invalid arguments.")
    return _OPERATIONS[operation](Workspace(root), **arguments)


def _reply(root: Path, request_file: Path | None) -> bytes:
    try:
        if request_file is None:
            result = _execute(root, sys.stdin.buffer)
        else:
            with request_file.open("rb") as stream:
                result = _execute(root, stream)
        response = {"version": WORKER_VERSION, "ok": True, "result": result}
    except (OSError, ValueError, TypeError, KeyError) as error:
        response = {"version": WORKER_VERSION, "ok": False, "error": str(error)[:600]}
    except ModuleNotFoundError:
        response = {
            "version": WORKER_VERSION,
            "ok": False,
            "error": "The sandbox runtime does not include Playwright.",
        }
    data = json.dumps(response, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(data) > MAX_RPC_BYTES:
        return json.dumps({
            "version": WORKER_VERSION,
            "ok": False,
            "error": "The response exceeds the workspace transfer limit.",
        }).encode("utf-8")
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(WORKSPACE_ROOT))
    parser.add_argument("--request", type=Path)
    parser.add_argument("--response", type=Path)
    args = parser.parse_args()
    if (args.request is None) != (args.response is None):
        parser.error("Provide both request and response paths or use standard I/O.")
    data = _reply(args.workspace, args.request)
    if args.response is None:
        sys.stdout.buffer.write(data)
    else:
        with args.response.open("xb") as stream:
            stream.write(data)


if __name__ == "__main__":
    main()
