import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Protocol

from agent_cook.state.session import Message
from agent_cook.tools.spec import ToolDefinition

MODEL_CALL_TIMEOUT_SECONDS = 300.0


class ModelCallTimeout(TimeoutError):
    """One model call exhausted its elapsed time budget."""


@asynccontextmanager
async def model_call_deadline() -> AsyncIterator[None]:
    deadline = asyncio.timeout(MODEL_CALL_TIMEOUT_SECONDS)
    try:
        async with deadline:
            yield
    except TimeoutError as error:
        if deadline.expired():
            raise ModelCallTimeout(f"The model call exceeded {MODEL_CALL_TIMEOUT_SECONDS:g} seconds.") from error
        raise


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
