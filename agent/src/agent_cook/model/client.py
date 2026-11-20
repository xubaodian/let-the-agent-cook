from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from agent_cook.state.session import Message
from agent_cook.tools.spec import ToolDefinition


@dataclass(frozen=True, slots=True)
class ModelTurn:
    message: Message
    finish_reason: str
    prompt_tokens: int | None = None
    web_usage: dict[str, int] = field(default_factory=dict)
    web_sources: tuple[dict[str, str], ...] = ()


class ModelClient(Protocol):
    async def next_turn(
        self,
        *,
        model: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        web_enabled: bool = False,
    ) -> ModelTurn: ...
