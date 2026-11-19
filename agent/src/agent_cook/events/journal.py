import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TextIO
from uuid import uuid4

from agent_cook.protocol.events import RunEvent


class RunEvents:
    def __init__(
        self,
        sink: Callable[[RunEvent], None] | None = None,
        *,
        run_id: str | None = None,
        parent: "RunEvents | None" = None,
    ) -> None:
        self.run_id = run_id or uuid4().hex
        self._sink = sink
        self._seq = 0
        self.parent_run_id = parent.run_id if parent is not None else None
        self._root = parent._root if parent is not None else self

    def child(self) -> "RunEvents":
        return RunEvents(self._sink, parent=self)

    def emit(self, event_type: str, **data: object) -> None:
        if self._sink is None:
            return
        self._root._seq += 1
        self._sink(
            RunEvent(
                run_id=self.run_id,
                root_run_id=self._root.run_id,
                parent_run_id=self.parent_run_id,
                seq=self._root._seq,
                timestamp=datetime.now(UTC).isoformat(),
                type=event_type,
                data=data,
            )
        )


def write_jsonl(stream: TextIO, event: RunEvent) -> None:
    line = json.dumps(asdict(event), ensure_ascii=False, allow_nan=False)
    stream.write(line + "\n")
    stream.flush()
