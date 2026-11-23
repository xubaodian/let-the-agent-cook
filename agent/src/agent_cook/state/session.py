from dataclasses import dataclass, field
from typing import Literal

from agent_cook.tools.spec import ToolCall


COMPLETION_CHECK_PREFIX = "Runtime completion check. "


@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    provider_data: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Summarize messages before this index in the original session."""

    covered_messages: int
    markdown: str


@dataclass(slots=True)
class Session:
    messages: list[Message] = field(default_factory=list)
    checkpoint: Checkpoint | None = None
    failed_tool_calls: set[str] = field(default_factory=set)
