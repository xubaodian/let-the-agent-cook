import base64
import io
import json
import os
import sys
import unittest
from contextlib import asynccontextmanager, redirect_stderr, redirect_stdout
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from agent_cook.cli import main
from agent_cook.model.openrouter_images import ImageGenerationError, OpenRouterImageGenerator
from agent_cook.tools.images import register_image_tool
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.spec import ToolCall, ToolContext
from test_openrouter import response, sdk_client
from test_workspace_tools import WorkerFixture


_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
    "AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class FixtureConnectionError(Exception):
    pass


class FixtureTimeoutError(FixtureConnectionError):
    pass


class FixtureStatusError(Exception):
    status_code = 400


def sdk_module(client=None):
    @asynccontextmanager
    async def open_client(**_):
        yield client

    return SimpleNamespace(
        AsyncOpenAI=open_client,
        APIConnectionError=FixtureConnectionError,
        APITimeoutError=FixtureTimeoutError,
        APIStatusError=FixtureStatusError,
    )


def call(name, arguments):
    function = SimpleNamespace(name=name, arguments=json.dumps(arguments))
    return SimpleNamespace(id=f"call-{name}", type="function", function=function)


@unittest.skipUnless(os.name == "posix", "The workspace worker runs in Linux containers.")
class ImageCliTest(unittest.TestCase):
    def test_cli_generates_and_exports_the_same_image_through_the_worker(self):
        arguments = {
            "prompt": "City skyline at dusk", "aspect_ratio": "16:9",
            "resolution": "2K", "output_dir": "site/assets",
        }
        first_turn = response(calls=[call("generate_image", arguments)], finish_reason="tool_calls")
        generated = {}

        async def next_turn(**request):
            latest = request["messages"][-1]
            if latest["role"] == "user":
                return first_turn
            if latest.get("tool_call_id") == "call-generate_image":
                generated.update(json.loads(latest["content"]))
                submit = call("submit_results", {"paths": [generated["path"]]})
                return response(calls=[submit], finish_reason="tool_calls")
            return response("Image delivered.")

        post = AsyncMock(return_value={"data": [{"b64_json": _PNG}]})
        create = AsyncMock(side_effect=next_turn)
        client = sdk_client(create)
        client.with_options = Mock(return_value=SimpleNamespace(post=post))
        environment = {
            "OPENROUTER_API_KEY": "test-key", "OPENROUTER_MODEL": "fixture/environment-chat",
            "OPENROUTER_IMAGE_MODEL": "fixture/environment-image",
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            factory = WorkerFixture(workspace)
            trace = root / "run.jsonl"
            request = root / "request.json"
            request.write_text(json.dumps({
                "task": "Deliver an image", "model": "fixture/chat", "image_model": "fixture/image",
            }), encoding="utf-8")
            with (
                patch.dict(os.environ, environment),
                patch.dict(sys.modules, {"openai": sdk_module(client)}),
                patch("agent_cook.cli.NativeSandboxFactory", return_value=factory),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()) as stderr,
            ):
                status = main(["--sandbox", "native", "--trace", str(trace), "--request", str(request)])

            self.assertEqual(status, 0, stderr.getvalue())
            raw_trace = trace.read_text(encoding="utf-8")
            artifact = next(
                event["data"]["artifact"] for line in raw_trace.splitlines()
                if (event := json.loads(line))["type"] == "artifact.created"
            )
            self.assertEqual((root / artifact["path"]).read_bytes(), base64.b64decode(_PNG))
            self.assertEqual((workspace / generated["path"]).read_bytes(), base64.b64decode(_PNG))
            self.assertEqual(artifact["media_type"], "image/png")
            self.assertNotIn(_PNG, raw_trace)
            self.assertNotIn("test-key", raw_trace)

        self.assertEqual(generated["media_type"], "image/png")
        self.assertEqual(generated["bytes"], len(base64.b64decode(_PNG)))
        self.assertEqual(PurePosixPath(generated["path"]).parent.as_posix(), "site/assets")
        self.assertEqual(factory.closed, 1)
        first_request = create.call_args_list[0].kwargs
        self.assertEqual(first_request["model"], "fixture/chat")
        tools = {tool["function"]["name"]: tool["function"] for tool in first_request["tools"]}
        self.assertIn("website hero", tools["generate_image"]["description"])
        self.assertIn("PowerPoint cover", tools["generate_image"]["description"])
        catalog = json.loads(first_request["messages"][0]["content"].splitlines()[-1])
        self.assertTrue({"image-assets", "pptx", "static-site"} <= {skill["name"] for skill in catalog})
        client.with_options.assert_called_once_with(max_retries=0)
        post.assert_awaited_once_with("/images", cast_to=object, body={
            "model": "fixture/image", "prompt": arguments["prompt"],
            "aspect_ratio": "16:9", "resolution": "2K",
        })


class ImageFailureTest(unittest.IsolatedAsyncioTestCase):
    async def test_image_generation_retries_rate_limits_and_stops_on_an_ambiguous_timeout(self):
        limited = FixtureStatusError("rate limited")
        limited.status_code = 429
        limited.response = SimpleNamespace(headers={"Retry-After": "0.8"})
        post = AsyncMock(side_effect=[limited, {"data": [{"b64_json": _PNG}]}])
        client = SimpleNamespace(with_options=Mock(return_value=SimpleNamespace(post=post)))
        generator = OpenRouterImageGenerator(client, "fixture/image")
        with (
            patch.dict(sys.modules, {"openai": sdk_module()}),
            patch("agent_cook.retry.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            image = await generator.generate("Skyline")
            self.assertEqual(image.content, base64.b64decode(_PNG))
            self.assertEqual(post.await_count, 2)
            sleep.assert_awaited_once_with(0.8)

            post.reset_mock()
            post.side_effect = FixtureTimeoutError()
            with self.assertRaisesRegex(ImageGenerationError, "timed out"):
                await generator.generate("Skyline")
            post.assert_awaited_once()

    async def test_invalid_paths_and_provider_failures_do_not_write_files(self):
        post = AsyncMock()
        client = SimpleNamespace(with_options=Mock(return_value=SimpleNamespace(post=post)))
        generator = OpenRouterImageGenerator(client, "fixture/image")
        registry = ToolRegistry()
        name = register_image_tool(registry, generator)
        sandbox = SimpleNamespace(put_file=AsyncMock())
        context = ToolContext("run", frozenset({name}), AsyncMock(return_value=sandbox))

        async def invoke(**arguments):
            return await registry.invoke(ToolCall("call", name, json.dumps(arguments)), context)

        with patch.dict(sys.modules, {"openai": sdk_module()}):
            result = await invoke(prompt="A skyline", output_dir="../outside")
            self.assertTrue(result.is_error)
            context.get_sandbox.assert_not_awaited()
            post.assert_not_awaited()

            post.side_effect = FixtureStatusError("private provider payload")
            result = await invoke(prompt="A skyline")
            self.assertTrue(result.is_error)
            self.assertIn("HTTP 400", result.model_content)
            self.assertNotIn("private provider payload", result.model_content)

            post.side_effect = None
            post.return_value = {"data": [{"b64_json": "!!!"}]}
            result = await invoke(prompt="A skyline")
            self.assertTrue(result.is_error)
            self.assertIn("invalid base64", result.model_content)

        sandbox.put_file.assert_not_awaited()
