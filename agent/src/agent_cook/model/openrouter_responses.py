from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from agent_cook.context.policy import OUTPUT_TOKEN_LIMIT
from agent_cook.model.client import ModelTurn
from agent_cook.model.openrouter import _field, _hosted_web_tools, _web_sources, _web_usage
from agent_cook.state.session import Message
from agent_cook.tools.spec import ToolCall, ToolDefinition

if TYPE_CHECKING:
    from openai import AsyncOpenAI

_OUTPUT_ITEMS = "responses_output_items"


class OpenRouterResponsesModel:
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
            "input": [item for message in messages for item in _encode_message(message)],
            "max_output_tokens": OUTPUT_TOKEN_LIMIT,
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        definitions = [
            {
                "type": "function", "name": tool.name, "description": tool.description,
                "parameters": tool.input_schema, "strict": False,
            }
            for tool in tools
        ]
        extra: dict[str, Any] = {}
        if tools:
            extra["provider"] = {"require_parameters": True}
        if web_enabled:
            extra["tools"] = [*definitions, *_hosted_web_tools()]
        elif definitions:
            request["tools"] = definitions
        if extra:
            request["extra_body"] = extra
        return _decode_response(await self._client.responses.create(**request), web_enabled=web_enabled)


def _encode_message(message: Message) -> Iterator[dict[str, Any]]:
    if message.role == "tool":
        yield {"type": "function_call_output", "call_id": message.tool_call_id, "output": message.content or ""}
        return
    if message.role == "assistant" and (items := message.provider_data.get(_OUTPUT_ITEMS)):
        yield from items
        return
    if message.content is not None:
        yield {"role": message.role, "content": message.content}
    for call in message.tool_calls:
        yield {"type": "function_call", "call_id": call.id, "name": call.name, "arguments": call.arguments}


def _decode_response(response: object, *, web_enabled: bool) -> ModelTurn:
    status = _field(response, "status")
    usage = _field(response, "usage")
    prompt_tokens = _field(usage, "input_tokens")
    prompt_tokens = prompt_tokens if type(prompt_tokens) is int and prompt_tokens > 0 else None
    if status != "completed":
        reason = _field(_field(response, "incomplete_details"), "reason")
        finish = reason if status == "incomplete" and reason else status or "failed"
        return ModelTurn(Message("assistant"), finish, prompt_tokens=prompt_tokens)

    content, refusals, calls, replay = [], [], [], []
    sources: dict[str, dict[str, str]] = {}
    for item in _field(response, "output") or ():
        kind = _field(item, "type")
        if kind == "message":
            for part in _field(item, "content") or ():
                if _field(part, "type") == "output_text":
                    content.append(_field(part, "text") or "")
                    if web_enabled:
                        sources.update((source["url"], source) for source in _web_sources(part))
                elif _field(part, "type") == "refusal":
                    refusals.append(_field(part, "refusal") or "")
        elif kind == "function_call":
            calls.append(ToolCall(
                _field(item, "call_id") or "", _field(item, "name"), _field(item, "arguments"),
            ))
        elif kind != "reasoning":
            # OpenRouter runs hosted tools and reports them separately in usage and citations.
            continue
        replay.append(dict(item) if isinstance(item, Mapping) else item.model_dump(mode="json", exclude_none=True))

    return ModelTurn(
        message=Message(
            "assistant", "\n".join(content or refusals) or None, tuple(calls),
            provider_data={_OUTPUT_ITEMS: replay} if replay else {},
        ),
        finish_reason="tool_calls" if calls else "stop",
        prompt_tokens=prompt_tokens,
        web_usage=_web_usage(usage) if web_enabled else {},
        web_sources=tuple(sources.values()),
    )
