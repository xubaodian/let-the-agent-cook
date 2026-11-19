import json
from collections.abc import Sequence
from dataclasses import asdict

from agent_cook.state.session import Message
from agent_cook.tools.spec import ToolDefinition


def estimate_text(text: str) -> int:
    return (len(text.encode("utf-8")) + 2) // 3


def estimate_messages(messages: Sequence[Message]) -> int:
    return sum(4 + estimate_text(_json(asdict(message))) for message in messages)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class PromptBudget:
    """Use the last observed input count while a request's prefix remains intact."""

    def __init__(self, tools: Sequence[ToolDefinition] = ()) -> None:
        self._tools = estimate_text(_json([asdict(tool) for tool in tools])) if tools else 0
        self._previous: tuple[Message, ...] = ()
        self._actual: int | None = None

    def estimate(self, messages: tuple[Message, ...]) -> int:
        if self._actual is not None and messages[:len(self._previous)] == self._previous:
            return self._actual + estimate_messages(messages[len(self._previous):])
        return self._tools + estimate_messages(messages)

    def observe(self, messages: tuple[Message, ...], prompt_tokens: int | None) -> None:
        self._previous = messages
        self._actual = prompt_tokens
