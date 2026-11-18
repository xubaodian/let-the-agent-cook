from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from agent_cook.model.client import ModelTurn
from agent_cook.state.session import Message
from agent_cook.tools.spec import ToolCall, ToolDefinition

if TYPE_CHECKING:
    from openai import AsyncOpenAI


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterModel:
    def __init__(self, client: "AsyncOpenAI") -> None:
        self._client = client

    async def next_turn(
        self,
        *,
        model: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelTurn:
        request: dict[str, Any] = {
            "model": model,
            "messages": [_encode_message(message) for message in messages],
        }
        if tools:
            request["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in tools
            ]
            request["extra_body"] = {"provider": {"require_parameters": True}}

        response = await self._client.chat.completions.create(**request)
        if not response.choices:
            raise ValueError("OpenRouter returned no model choices.")

        choice = response.choices[0]
        raw_message = choice.message
        calls = []
        for call in raw_message.tool_calls or ():
            if call.type != "function":
                raise ValueError("OpenRouter returned an unsupported tool call type.")
            calls.append(ToolCall(call.id, call.function.name, call.function.arguments))

        provider_data = {}
        reasoning_details = getattr(raw_message, "reasoning_details", None)
        if reasoning_details is not None:
            provider_data["reasoning_details"] = reasoning_details

        return ModelTurn(
            message=Message(
                role="assistant",
                content=raw_message.content or raw_message.refusal,
                tool_calls=tuple(calls),
                provider_data=provider_data,
            ),
            finish_reason=choice.finish_reason,
        )


def _encode_message(message: Message) -> dict[str, object]:
    result: dict[str, object] = {"role": message.role, "content": message.content}
    if message.role == "tool":
        result["tool_call_id"] = message.tool_call_id
    if message.role == "assistant":
        if message.tool_calls:
            result["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in message.tool_calls
            ]
        if "reasoning_details" in message.provider_data:
            result["reasoning_details"] = message.provider_data["reasoning_details"]
    return result
