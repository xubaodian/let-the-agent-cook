import io
import json
import os
import stat
import unittest
from contextlib import redirect_stderr, redirect_stdout
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import httpx

from agent_cook.cli import main
from agent_cook.tools.spec import ToolResult
from test_openrouter_responses import function_item, reply, text_item


def chat_reply(content=None, *, calls=()):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {
                "id": item["call_id"], "type": "function",
                "function": {"name": item["name"], "arguments": item["arguments"]},
            }
            for item in calls
        ]
    return {
        "id": "fixture", "object": "chat.completion", "created": 1, "model": "fixture/model",
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
    }


@unittest.skipUnless(find_spec("openai"), "Requires the locked OpenAI SDK.")
class ModelDiagnosticsTest(unittest.TestCase):
    def test_cli_records_wire_bodies_and_tool_arguments_without_changing_replay(self):
        task = "查杭州未来七天天气。"
        search = function_item("web_search", "search", query="H")
        fetch = function_item("web_fetch", "fetch", url="https://")
        openrouter_key, exa_key = "test-openrouter-credential", "test-exa-credential"

        for api, endpoint, calls, final in (
            ("chat_completions", "chat/completions", chat_reply(calls=(search, fetch)), chat_reply("Done.")),
            ("responses", "responses", reply(search, fetch), reply(text_item("Done."))),
        ):
            wire_requests = []
            outputs = iter([
                (503, {"error": {"message": "Please retry."}}),
                (200, {**calls, "provider": "FixtureProvider", "reflected_key": openrouter_key}),
                (200, final),
            ])

            def respond(request):
                self.assertEqual(request.url.path, f"/api/v1/{endpoint}")
                wire_requests.append(request.content.decode("utf-8"))
                code, body = next(outputs)
                return httpx.Response(code, json=body, headers={
                    "retry-after-ms": "1", "x-request-id": f"request-{len(wire_requests)}",
                })

            with (
                self.subTest(api=api),
                TemporaryDirectory() as directory,
                patch.dict(os.environ, {
                    "OPENROUTER_API_KEY": openrouter_key, "EXA_API_KEY": exa_key,
                    "OPENROUTER_MODEL": "fixture/model",
                }, clear=True),
                patch("agent_cook.runner.AsyncClient", side_effect=lambda **kwargs: httpx.AsyncClient(
                    transport=httpx.MockTransport(respond), **kwargs,
                )),
                patch("agent_cook.tools.web.call_mcp_tool", new=AsyncMock(
                    return_value=ToolResult("Exa received H."),
                )) as exa,
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as stderr,
            ):
                trace = Path(directory) / "run.jsonl"
                self.assertEqual(main([
                    "exec", "-C", directory, "--sandbox", "none", "--trace", str(trace),
                    "--model-api", api, "--json", task,
                ]), 0, stderr.getvalue())
                private = trace.with_suffix(".model.ndjson")
                raw = private.read_text(encoding="utf-8")
                self.assertIn(str(private), stderr.getvalue())
                if os.name == "posix":
                    self.assertEqual(stat.S_IMODE(private.stat().st_mode) & 0o077, 0)
                self.assertNotIn(openrouter_key, raw)
                self.assertNotIn(exa_key, raw)
                self.assertEqual(trace.read_text(encoding="utf-8"), stdout.getvalue())
                self.assertNotIn('"type": "model.response"', stdout.getvalue())
                events = [json.loads(line) for line in raw.splitlines()]
                requests = [event["data"] for event in events if event["type"] == "model.request"]
                responses = [event["data"] for event in events if event["type"] == "model.response"]
                self.assertEqual([event["body"] for event in requests], wire_requests)
                self.assertEqual([event["request_id"] for event in requests], [event["request_id"] for event in responses])
                self.assertEqual(len({event["request_id"] for event in requests}), 3)
                self.assertEqual([event["status_code"] for event in responses], [503, 200, 200])
                self.assertEqual(responses[1]["provider_request_id"], "request-2")
                self.assertEqual(json.loads(responses[1]["body"]), {
                    **calls, "provider": "FixtureProvider", "reflected_key": "[redacted]",
                })
                self.assertIn(task, requests[0]["body"])

                tool_calls = [event["data"] for event in events if event["type"] == "tool.call"]
                tool_results = [event["data"] for event in events if event["type"] == "tool.result"]
                self.assertEqual([event["arguments"] for event in tool_calls], [search["arguments"], fetch["arguments"]])
                self.assertEqual([event["tool_call_id"] for event in tool_calls], ["search", "fetch"])
                self.assertEqual([event["tool_call_id"] for event in tool_results], ["search", "fetch"])
                self.assertTrue(tool_results[1]["is_error"])
                self.assertIn("including its hostname", tool_results[1]["output"])
                self.assertEqual([
                    event["data"]["arguments"] for event in events if event["type"] == "exa.request"
                ], [{"query": "H", "numResults": 5}])
                self.assertEqual(exa.await_count, 1)
                self.assertEqual(exa.await_args.args[1:], ("web_search_exa", {"query": "H", "numResults": 5}))
