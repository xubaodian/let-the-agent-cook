from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from agent_cook.state.session import Message
from agent_cook.tools.spec import ToolDefinition


@dataclass(frozen=True, slots=True)
class ModelTurn:
    message: Message
    finish_reason: str
    prompt_tokens: int | None = None


class ModelClient(Protocol):
    async def next_turn(
        self,
        *,
        model: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelTurn: ...
