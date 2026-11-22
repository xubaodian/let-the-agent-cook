import json
import unittest
from importlib.util import find_spec
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent_cook.model.openrouter import OPENROUTER_BASE_URL
from agent_cook.model.openrouter_responses import OpenRouterResponsesModel
from agent_cook.state.session import Message
from agent_cook.tools.spec import ToolCall, ToolDefinition


def text_item(text, annotations=()):
    return {
        "type": "message", "id": "msg_fixture", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": list(annotations)}],
    }


def reply(*items, status="completed", prompt_tokens=140):
    return {
        "id": "resp_fixture", "object": "response", "created_at": 1, "model": "fixture/model",
        "status": status, "output": list(items), "parallel_tool_calls": False,
        "tools": [], "tool_choice": "auto",
        "usage": {"input_tokens": prompt_tokens, "output_tokens": 25, "total_tokens": prompt_tokens + 25},
    }


def function_item(name, call_id, **arguments):
    return {
        "type": "function_call", "id": f"fc_{call_id}", "call_id": call_id, "name": name,
        "arguments": json.dumps(arguments, ensure_ascii=False), "status": "completed",
    }


class ResponsesTest(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(find_spec("openai"), "Requires the locked OpenAI SDK.")
    async def test_sdk_round_trip_resends_reasoning_and_pairs_tools_by_call_id(self):
        import httpx
        from openai import AsyncOpenAI

        reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"}
        call = function_item("read_file", "call_1", path="pptx/SKILL.md")
        responses = iter([reply(reasoning, call, prompt_tokens=216), reply(text_item("Done."))])
        requests = []

        def respond(request):
            self.assertEqual(request.url.path, "/api/v1/responses")
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=next(responses))

        tool = ToolDefinition("read_file", "Read a workspace file.", {
            "type": "object", "properties": {"path": {"type": "string"}},
        })
        messages = [Message("system", "Build the deck."), Message("user", "Create four slides.")]
        async with AsyncOpenAI(
            api_key="fixture", base_url=OPENROUTER_BASE_URL,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        ) as sdk:
            adapter = OpenRouterResponsesModel(sdk)
            first = await adapter.next_turn(model="fixture/model", messages=messages, tools=[tool])
            self.assertEqual(first.finish_reason, "tool_calls")
            self.assertEqual(first.prompt_tokens, 216)
            self.assertEqual(first.message.tool_calls[0].id, "call_1")
            second = await adapter.next_turn(
                model="fixture/model",
                messages=[*messages, first.message, Message("tool", "PowerPoint instructions", tool_call_id="call_1")],
                tools=[tool],
            )

        self.assertEqual(second.message.content, "Done.")
        self.assertEqual(second.finish_reason, "stop")
        self.assertEqual([request["max_output_tokens"] for request in requests], [50_000, 50_000])
        self.assertFalse(requests[0]["store"])
        self.assertNotIn("previous_response_id", requests[0])
        self.assertEqual(requests[0]["include"], ["reasoning.encrypted_content"])
        self.assertEqual(requests[0]["tools"], [
            {"type": "function", "name": "read_file", "description": tool.description,
             "parameters": tool.input_schema, "strict": False},
        ])
        self.assertEqual(requests[0]["input"], requests[1]["input"][:2])
        carried_reasoning, carried_call, result = requests[1]["input"][2:]
        self.assertEqual(carried_reasoning["encrypted_content"], "opaque")
        self.assertEqual(carried_call["id"], "fc_call_1")
        self.assertEqual(result, {
            "type": "function_call_output", "call_id": "call_1", "output": "PowerPoint instructions",
        })

    async def test_historical_chat_tool_calls_and_hosted_web_usage(self):
        response = reply(
            {"type": "web_search_call", "id": "ws_1"},
            text_item("Found the source.", [{"type": "url_citation", "url": "https://example.com", "title": "Example"}]),
        )
        response["usage"]["server_tool_use_details"] = {"web_search_requests": 1}
        create = AsyncMock(return_value=response)
        adapter = OpenRouterResponsesModel(SimpleNamespace(responses=SimpleNamespace(create=create)))
        turn = await adapter.next_turn(
            model="fixture/model",
            messages=[
                Message("assistant", tool_calls=(ToolCall("old", "read_file", "{}"),),
                        provider_data={"reasoning_details": ["chat-only data"]}),
                Message("tool", "old result", tool_call_id="old"),
            ], tools=(), web_enabled=True,
        )
        self.assertEqual(turn.web_usage, {"web_search_requests": 1})
        self.assertEqual(turn.web_sources, ({"url": "https://example.com", "title": "Example"},))
        self.assertEqual(turn.message.content, "Found the source.")
        self.assertEqual(len(turn.message.provider_data["responses_output_items"]), 1)
        self.assertEqual(create.call_args.kwargs["input"], [
            {"type": "function_call", "call_id": "old", "name": "read_file", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "old", "output": "old result"},
        ])
        self.assertEqual([tool["type"] for tool in create.call_args.kwargs["extra_body"]["tools"]], [
            "openrouter:web_search", "openrouter:web_fetch",
        ])

    async def test_incomplete_response_does_not_expose_partial_tool_call(self):
        response = reply(function_item("write_file", "partial"), status="incomplete")
        response["incomplete_details"] = {"reason": "max_output_tokens"}
        create = AsyncMock(return_value=response)
        adapter = OpenRouterResponsesModel(SimpleNamespace(responses=SimpleNamespace(create=create)))
        turn = await adapter.next_turn(model="fixture/model", messages=[Message("user", "Start.")], tools=())
        self.assertEqual(create.call_args.kwargs["max_output_tokens"], 50_000)
        self.assertEqual(turn.finish_reason, "max_output_tokens")
        self.assertFalse(turn.message.tool_calls)
