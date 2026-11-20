from collections.abc import Mapping, Sequence
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
        web_enabled: bool = False,
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
        if web_enabled:
            # The SDK's typed chat tools do not describe OpenRouter's hosted tool types.
            request.setdefault("extra_body", {})["tools"] = [
                *request.pop("tools", []),
                {
                    "type": "openrouter:web_search",
                    "parameters": {"max_uses": 3, "max_results": 5, "max_total_results": 15},
                },
                {"type": "openrouter:web_fetch", "parameters": {"max_uses": 3, "max_content_tokens": 4000}},
            ]

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
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None)

        return ModelTurn(
            message=Message(
                role="assistant",
                content=raw_message.content or raw_message.refusal,
                tool_calls=tuple(calls),
                provider_data=provider_data,
            ),
            finish_reason=choice.finish_reason,
            prompt_tokens=prompt_tokens if type(prompt_tokens) is int and prompt_tokens > 0 else None,
            web_usage=_web_usage(usage) if web_enabled else {},
            web_sources=_web_sources(raw_message) if web_enabled else (),
        )


def _field(value: object, key: str) -> Any:
    return value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)


def _web_usage(usage: object) -> dict[str, int]:
    details = _field(usage, "server_tool_use_details") or _field(usage, "server_tool_use")
    names = ("web_search_requests", "tool_calls_requested", "tool_calls_executed")
    return {name: count for name in names if type(count := _field(details, name)) is int and count >= 0}


def _web_sources(message: object) -> tuple[dict[str, str], ...]:
    sources: dict[str, dict[str, str]] = {}
    for annotation in _field(message, "annotations") or ():
        if _field(annotation, "type") != "url_citation":
            continue
        citation = _field(annotation, "url_citation") or annotation
        url = _field(citation, "url")
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            title = _field(citation, "title")
            sources[url] = {"url": url, "title": title if isinstance(title, str) else ""}
    return tuple(sources.values())


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
