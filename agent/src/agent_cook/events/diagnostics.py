import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from httpx import Request, Response

from agent_cook.events.journal import RunEvents, write_jsonl
from agent_cook.protocol.events import RunEvent
from agent_cook.settings import restrict_windows_file


@contextmanager
def local_model_trace(path: Path, run_id: str, credentials: tuple[str, ...]) -> Iterator[RunEvents | None]:
    try:
        stream = open(
            path, "x", encoding="utf-8", errors="backslashreplace",
            opener=lambda filename, flags: os.open(filename, flags, 0o600),
        )
        try:
            if sys.platform == "win32":
                restrict_windows_file(path)
        except OSError:
            stream.close()
            raise
    except OSError as error:
        print(f"Could not open model trace at {path}: {error}", file=sys.stderr)
        yield None
        return

    enabled = True
    secrets = tuple(sorted(filter(None, credentials), key=len, reverse=True))

    def redact(value: Any) -> Any:
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "[redacted]")
        elif isinstance(value, list):
            value = [redact(item) for item in value]
        elif isinstance(value, dict):
            value = {key: redact(item) for key, item in value.items()}
        return value

    def record(event: RunEvent) -> None:
        nonlocal enabled
        if enabled:
            try:
                write_jsonl(stream, replace(event, data=redact(event.data)))
            except OSError as error:
                enabled = False
                print(f"Could not continue writing model trace at {path}: {error}", file=sys.stderr)

    print(f"Model trace: {path}", file=sys.stderr)
    try:
        yield RunEvents(record, run_id=run_id)
    finally:
        with suppress(OSError):
            stream.close()


def model_http_hooks(events: RunEvents) -> dict:
    async def request_sent(request: Request) -> None:
        request_id = request.extensions["agent_cook_request_id"] = uuid4().hex
        events.emit(
            "model.request",
            request_id=request_id,
            endpoint=request.url.path,
            body=request.content.decode("utf-8", errors="replace"),
        )

    async def response_received(response: Response) -> None:
        await response.aread()
        events.emit(
            "model.response",
            request_id=response.request.extensions["agent_cook_request_id"],
            provider_request_id=response.headers.get("x-request-id"),
            status_code=response.status_code,
            body=response.text,
        )

    return {"request": [request_sent], "response": [response_received]}
