import asyncio
import io
import json
import os
import sys
import unittest
from contextlib import asynccontextmanager, redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent_cook.cli import main
from agent_cook.context.policy import DEFAULT_CONTEXT_POLICY
from agent_cook.context.prompts import CHECKPOINT_HEADINGS
from agent_cook.core.run import run_agent
from agent_cook.core.types import AgentRunError, AgentSpec
from agent_cook.events.journal import RunEvents
from agent_cook.model.client import ModelTurn
from agent_cook.sandbox.client import WorkerSandbox
from agent_cook.sandbox.interface import SandboxError
from agent_cook.sandbox.limits import MAX_COMMAND_OUTPUT, WORKER_MODULE
from agent_cook.state.session import Message
from agent_cook.tools.artifacts import ArtifactExporter
from agent_cook.tools.spec import ToolCall, ToolContext
from agent_cook.tools.workspace import workspace_tools
from test_openrouter import response, sdk_client


class WorkerFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.closed = 0
        self.sandbox = WorkerSandbox(self.exchange)

    @asynccontextmanager
    async def open(self, run_id):
        try:
            yield self.sandbox
        finally:
            self.closed += 1

    async def exchange(self, request: bytes, timeout: float) -> bytes:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", WORKER_MODULE, "--workspace", str(self.root),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(request), timeout)
        finally:
            if process.returncode is None:
                process.kill()
                await process.communicate()
        if process.returncode:
            raise AssertionError(stderr.decode("utf-8", errors="replace"))
        return stdout


@unittest.skipUnless(os.name == "posix", "The workspace worker runs in Linux containers.")
class WorkspaceToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        root = self.directory / "workspace"
        root.mkdir()
        self.worker = WorkerFixture(root)
        self.sandbox = self.worker.sandbox

    async def test_agent_loop_seeds_files_and_uses_the_real_worker(self):
        output = self.directory / "artifacts"
        registry, names = workspace_tools(ArtifactExporter(output))
        steps = [
            ("ls", {}),
            ("read_file", {"path": "input.csv", "start_line": 2, "line_count": 1}),
            ("copy_file", {"source": "input.csv", "destination": "results/copy.csv"}),
            ("search_files", {"glob": "**/*.csv", "query": "JAN"}),
            ("write_file", {"path": "note.txt", "content": "Complete\n"}),
            ("run_command", {
                "argv": [sys.executable, "-c", "import pathlib; print(pathlib.Path('note.txt').read_text())"],
            }),
            ("submit_results", {"paths": ["results/copy.csv", "note.txt"]}),
        ]
        calls = tuple(
            ToolCall(str(index), name, json.dumps(arguments))
            for index, (name, arguments) in enumerate(steps, 1)
        )
        model = AsyncMock()
        model.next_turn.side_effect = [
            ModelTurn(Message("assistant", tool_calls=calls), "tool_calls"),
            ModelTurn(Message("assistant", "Submitted."), "stop"),
        ]
        trace = []
        original = b"month,amount\nJan,100\nFeb,105\n"

        result = await run_agent(
            AgentSpec("fixture/model", tool_names=names),
            "Process the input and submit the files",
            model=model,
            tools=registry,
            sandbox_factory=self.worker,
            input_files={"input.csv": original},
            events=RunEvents(trace.append),
        )

        messages = model.next_turn.call_args.kwargs["messages"]
        payloads = [json.loads(message.content) for message in messages if message.role == "tool"]
        self.assertEqual(payloads[0]["entries"], [{"path": "input.csv", "kind": "file"}])
        self.assertEqual(payloads[1]["content"], "Jan,100\n")
        self.assertEqual(payloads[1]["next_line"], 3)
        self.assertEqual([match["line"] for match in payloads[3]["matches"]], [2, 2])
        self.assertEqual(payloads[5]["stdout"].strip(), "Complete")
        self.assertEqual(result.text, "Submitted.")
        self.assertEqual([artifact.media_type for artifact in result.artifacts], ["text/csv", "text/plain"])
        self.assertEqual((self.directory / result.artifacts[0].path).read_bytes(), original)
        self.assertEqual(self.worker.closed, 1)
        self.assertEqual([event.seq for event in trace], list(range(1, len(trace) + 1)))
        self.assertEqual(sum(event.type == "artifact.created" for event in trace), 2)
        self.assertEqual(trace[-1].type, "run.completed")

    async def test_agent_finishes_only_after_submitting_the_current_file(self):
        output = self.directory / "artifacts"
        registry, names = workspace_tools(ArtifactExporter(output))
        model = AsyncMock()
        model.next_turn.side_effect = [
            ModelTurn(Message("assistant", "Premature answer."), "stop"),
            ModelTurn(Message("assistant", tool_calls=(
                ToolCall("draft", "write_file", '{"path":"page.txt","content":"draft"}'),
                ToolCall("submit-draft", "submit_results", '{"paths":["page.txt"]}'),
                ToolCall("update", "run_command", json.dumps({"argv": [
                    sys.executable, "-c", "from pathlib import Path; Path('page.txt').write_text('final')",
                ]})),
            )), "tool_calls"),
            ModelTurn(Message("assistant", "The older file was submitted."), "stop"),
            ModelTurn(Message("assistant", tool_calls=(
                ToolCall("submit-final", "submit_results", '{"paths":["page.txt"]}'),
                ToolCall("list", "ls", "{}"),
            )), "tool_calls", prompt_tokens=25_000),
            ModelTurn(Message(
                "assistant", "\n\n".join(f"## {heading}\nThe current page was submitted." for heading in CHECKPOINT_HEADINGS),
            ), "stop"),
            ModelTurn(Message("assistant", "Final file submitted."), "stop"),
        ]
        trace = []

        result = await run_agent(
            AgentSpec(
                "fixture/model", tool_names=names, max_model_turns=5,
                context_policy=replace(DEFAULT_CONTEXT_POLICY, keep_recent_turns=0),
            ),
            "Deliver the current page.",
            model=model, tools=registry, sandbox_factory=self.worker, events=RunEvents(trace.append),
        )

        requests = model.next_turn.call_args_list
        self.assertIn("Call submit_results to submit the final files", requests[1].kwargs["messages"][-1].content)
        self.assertIn("changed or are missing in the workspace: page.txt", requests[3].kwargs["messages"][-1].content)
        self.assertEqual(result.text, "Final file submitted.")
        artifact, = result.artifacts
        self.assertEqual((self.directory / artifact.path).read_text(encoding="utf-8"), "final")
        self.assertEqual([event.type for event in trace].count("artifact.created"), 2)
        self.assertEqual([event.type for event in trace].count("run.submission_required"), 2)
        self.assertEqual([event.type for event in trace].count("context.compacted"), 1)
        self.assertEqual(self.worker.closed, 1)

    async def test_failed_submit_does_not_complete_the_run(self):
        registry, names = workspace_tools(ArtifactExporter(self.directory / "artifacts"))
        model = AsyncMock()
        model.next_turn.side_effect = [
            ModelTurn(Message("assistant", tool_calls=(
                ToolCall("missing", "submit_results", '{"paths":["missing.txt"]}'),
            )), "tool_calls"),
            ModelTurn(Message("assistant", "Submitted."), "stop"),
        ]
        with self.assertRaisesRegex(AgentRunError, "Call submit_results"):
            await run_agent(
                AgentSpec("fixture/model", tool_names=names, max_model_turns=2), "Deliver the file.",
                model=model, tools=registry, sandbox_factory=self.worker,
            )
        self.assertEqual(self.worker.closed, 1)

    async def test_file_calls_preserve_binary_data_and_block_workspace_escape(self):
        private = self.directory / "outside.txt"
        private.write_text("outside", encoding="utf-8")
        (self.worker.root / "link.txt").symlink_to(private)
        (self.worker.root / "linked-dir").symlink_to(self.directory, target_is_directory=True)
        for path in ("../outside.txt", "link.txt", "linked-dir/outside.txt"):
            with self.subTest(path=path), self.assertRaises((ValueError, SandboxError)):
                await self.sandbox.get_file(path)
        with self.assertRaises(SandboxError):
            await self.sandbox.request("write_file", {"path": "link.txt", "content": "wrong"})
        self.assertEqual(private.read_text(encoding="utf-8"), "outside")

        binary = b"\0\xff\x80\x01"
        await self.sandbox.put_file("src.dat", binary)
        await self.sandbox.request("copy_file", {"source": "src.dat", "destination": "dist/src.dat"})
        self.assertEqual(await self.sandbox.get_file("dist/src.dat"), binary)
        with self.assertRaises(SandboxError):
            await self.sandbox.request("copy_file", {"source": "src.dat", "destination": "dist/src.dat"})
        self.assertEqual(await self.sandbox.get_file("dist/src.dat"), binary)

    async def test_ls_filters_and_pages_workspace_paths(self):
        for path in ("notes.txt", "src/a.py", "src/sub/b.py", "src/sub/c.py"):
            await self.sandbox.put_file(path, b"example")
        registry, names = workspace_tools(ArtifactExporter(self.directory / "artifacts"))
        context = ToolContext("fixture", frozenset(names), AsyncMock(return_value=self.sandbox))

        async def invoke(tool, **arguments):
            result = await registry.invoke(ToolCall(tool, tool, json.dumps(arguments)), context)
            self.assertFalse(result.is_error, result.model_content)
            return json.loads(result.model_content)

        root = await invoke("ls")
        self.assertEqual(root["entries"], [
            {"path": "notes.txt", "kind": "file"}, {"path": "src", "kind": "directory"},
        ])
        directories = await invoke("ls", recursive=True, kind="directory")
        self.assertEqual([entry["path"] for entry in directories["entries"]], ["src", "src/sub"])

        options = {"directory": "src", "recursive": True, "kind": "file", "max_results": 2}
        first = await invoke("ls", **options)
        self.assertEqual([entry["path"] for entry in first["entries"]], ["src/a.py", "src/sub/b.py"])
        self.assertTrue(first["truncated"])
        second = await invoke("ls", cursor=first["next_cursor"], **options)
        self.assertEqual(second["entries"], [{"path": "src/sub/c.py", "kind": "file"}])
        self.assertFalse(second["truncated"])
        self.assertIsNone(second["next_cursor"])

    async def test_result_export_is_atomic_when_one_file_cannot_be_read(self):
        output = self.directory / "artifacts"
        exporter = ArtifactExporter(output)
        await self.sandbox.put_file("valid.txt", b"valid")
        with self.assertRaises(SandboxError):
            await exporter.export(self.sandbox, ["valid.txt", "missing.txt"])
        self.assertEqual(list(output.iterdir()), [])
        artifacts = await exporter.export(self.sandbox, ["valid.txt"])
        self.assertEqual((self.directory / artifacts[0].path).read_bytes(), b"valid")

    async def test_commands_have_output_and_time_limits_and_do_not_inherit_model_keys(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "fixture-key"}):
            output = await self.sandbox.exec(
                [sys.executable, "-c", "import os; print(os.getenv('OPENROUTER_API_KEY')); print('x' * 40000)"],
                cwd=".",
                timeout_seconds=5,
            )
        self.assertEqual(output.exit_code, 0)
        self.assertTrue(output.truncated)
        self.assertEqual(len(output.stdout), MAX_COMMAND_OUTPUT)
        self.assertTrue(output.stdout.startswith("None\n"))
        timed = await self.sandbox.exec(
            [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(8)"],
            cwd=".",
            timeout_seconds=0.5,
        )
        self.assertTrue(timed.timed_out)
        self.assertNotEqual(timed.exit_code, 0)
        self.assertIn("started", timed.stdout)


@unittest.skipUnless(os.name == "posix", "The workspace worker runs in Linux containers.")
class WorkspaceCliTest(unittest.TestCase):
    def test_sandbox_cli_stages_an_input_and_exports_a_result_next_to_the_trace(self):
        calls = [
            SimpleNamespace(
                id="deliver",
                type="function",
                function=SimpleNamespace(
                    name="submit_results", arguments='{"paths":["input.txt"]}'
                ),
            )
        ]
        create = AsyncMock(side_effect=[
            response(calls=calls, finish_reason="tool_calls"), response("Delivered."),
        ])
        client = sdk_client(create)

        @asynccontextmanager
        async def open_client(**_):
            yield client

        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            factory = WorkerFixture(workspace)
            source = root / "input.txt"
            source.write_bytes(b"an explicit input")
            trace = root / "trace.jsonl"
            stdout = io.StringIO()
            stderr = io.StringIO()
            env = {
                "OPENROUTER_API_KEY": "test-key", "OPENROUTER_MODEL": "fixture/model",
                "OPENROUTER_IMAGE_MODEL": "",
            }
            with (
                patch.dict(os.environ, env),
                patch.dict(sys.modules, {"openai": SimpleNamespace(AsyncOpenAI=open_client)}),
                patch("agent_cook.cli.DockerSandboxFactory", return_value=factory),
                patch("agent_cook.cli.prepare_docker"),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                status = main([
                    "--sandbox", "docker", "--input", str(source), "--trace", str(trace),
                    "Export the input",
                ])
            event = next(
                event for line in trace.read_text().splitlines()
                if (event := json.loads(line))["type"] == "artifact.created"
            )
            file = root / event["data"]["artifact"]["path"]
            self.assertEqual(file.read_bytes(), source.read_bytes())
            delivered, = root.glob("*-results")
            self.assertEqual((delivered / "input.txt").read_bytes(), source.read_bytes())
            self.assertIn(str(delivered), stderr.getvalue())

        self.assertEqual(status, 0)
        self.assertEqual(stdout.getvalue(), "Delivered.\n")
        tools = create.call_args_list[0].kwargs["tools"]
        names = [tool["function"]["name"] for tool in tools]
        self.assertEqual(len(names), 8)
        self.assertIn("ls", names)
        self.assertNotIn("list_files", names)
        self.assertEqual(factory.closed, 1)
