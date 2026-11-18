from dataclasses import dataclass, field
from typing import Literal

from agent_cook.tools.spec import ToolCall


@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    provider_data: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class Session:
    messages: list[Message] = field(default_factory=list)
