import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent_cook.cli import main
from agent_cook.model.openrouter import OPENROUTER_BASE_URL, OpenRouterModel
from agent_cook.state.session import Message
from agent_cook.tools.spec import ToolDefinition


def response(content=None, *, calls=None, reasoning_details=None, finish_reason="stop", prompt_tokens=None):
    message = SimpleNamespace(
        content=content, refusal=None, tool_calls=calls, reasoning_details=reasoning_details
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens) if prompt_tokens is not None else None,
    )


def sdk_client(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


class OpenRouterTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_ids_and_provider_continuation_data_round_trip(self):
        call = SimpleNamespace(
            id="call-1", type="function", function=SimpleNamespace(name="fixture", arguments='{}')
        )
        details = [{"type": "reasoning.encrypted", "data": "fixture"}]
        create = AsyncMock(side_effect=[
            response(calls=[call], reasoning_details=details, finish_reason="tool_calls", prompt_tokens=123),
            response("Done."),
        ])
        adapter = OpenRouterModel(sdk_client(create))
        tools = (ToolDefinition("fixture", "Return a fixture value.", {"type": "object"}),)
        user = Message("user", "Use the fixture")

        first = await adapter.next_turn(model="fixture/model", messages=(user,), tools=tools)
        output = Message("tool", "value", tool_call_id=first.message.tool_calls[0].id)
        second = await adapter.next_turn(
            model="fixture/model", messages=(user, first.message, output), tools=tools
        )

        request = create.call_args.kwargs
        self.assertEqual(first.prompt_tokens, 123)
        self.assertIsNone(second.prompt_tokens)
        self.assertEqual(request["tools"][0]["function"]["parameters"], {"type": "object"})
        self.assertTrue(request["extra_body"]["provider"]["require_parameters"])
        self.assertEqual(request["messages"][1]["reasoning_details"], details)
        self.assertEqual(request["messages"][1]["tool_calls"][0]["id"], "call-1")
        self.assertEqual(request["messages"][2], {
            "role": "tool", "content": "value", "tool_call_id": "call-1",
        })


class CliTest(unittest.TestCase):
    def test_cli_writes_an_ordered_trace_and_keeps_stdout_for_the_answer(self):
        create = AsyncMock(return_value=response("The kitchen is ready."))
        client = sdk_client(create)
        constructor_args = []

        class ClientContext:
            def __init__(self, **kwargs):
                constructor_args.append(kwargs)

            async def __aenter__(self):
                return client

            async def __aexit__(self, *_):
                return None

        with TemporaryDirectory() as directory:
            trace = Path(directory) / "run.jsonl"
            stdout = io.StringIO()
            stderr = io.StringIO()
            sdk = SimpleNamespace(AsyncOpenAI=ClientContext)
            environment = {"OPENROUTER_API_KEY": "test-key", "OPENROUTER_MODEL": "fixture/model"}
            with (
                patch.dict(os.environ, environment),
                patch.dict(sys.modules, {"openai": sdk}),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                status = main(["Say hello", "--trace", str(trace)])
                duplicate_status = main(["Try again", "--trace", str(trace)])
            raw_trace = trace.read_text(encoding="utf-8")
            events = [json.loads(line) for line in raw_trace.splitlines()]

        self.assertEqual(status, 0)
        self.assertEqual(duplicate_status, 1)
        self.assertEqual(stdout.getvalue(), "The kitchen is ready.\n")
        self.assertEqual(constructor_args, [{"api_key": "test-key", "base_url": OPENROUTER_BASE_URL}])
        self.assertNotIn("tools", create.call_args.kwargs)
        self.assertNotIn("test-key", raw_trace)
        self.assertEqual([event["seq"] for event in events], [1, 2, 3, 4])
        self.assertEqual(events[-1]["data"]["text"], "The kitchen is ready.")
