from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunEvent:
    run_id: str
    root_run_id: str
    parent_run_id: str | None
    seq: int
    timestamp: str
    type: str
    data: dict[str, object]
    schema_version: int = 1
