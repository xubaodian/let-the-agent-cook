import io
import json
import os
import sys
import unittest
from contextlib import asynccontextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent_cook.cli import main
from agent_cook.state.local import LocalSessions
from agent_cook.terminal import file_mentions
from test_openrouter import response, sdk_client
from test_workspace_tools import WorkerFixture

ENV = {
    "OPENROUTER_API_KEY": "fixture-key", "OPENROUTER_MODEL": "fixture/model",
    "OPENROUTER_IMAGE_MODEL": "", "AGENT_COOK_SANDBOX": "",
}


def sdk(create):
    @asynccontextmanager
    async def client(**options):
        yield sdk_client(create)

    return SimpleNamespace(AsyncOpenAI=client)


def tool(turn, name, **arguments):
    function = SimpleNamespace(name=name, arguments=json.dumps(arguments))
    return response(
        calls=[SimpleNamespace(id=f"call-{turn}", type="function", function=function)], finish_reason="tool_calls",
    )


class CliEntryTest(unittest.TestCase):
    def test_exec_accepts_stdin_and_streams_only_events_on_stdout(self):
        create = AsyncMock(return_value=response("Done."))
        with (
            TemporaryDirectory() as directory,
            patch.dict(os.environ, ENV),
            patch.dict(sys.modules, {"openai": sdk(create)}),
            patch("agent_cook.cli.prepare_docker") as docker,
            patch("sys.stdin", io.StringIO("Build a summary.")),
            redirect_stdout(io.StringIO()) as output,
            redirect_stderr(io.StringIO()),
        ):
            status = main(["exec", "-C", directory, "--json", "-"])
            trace = next((Path(directory) / ".agent-cook" / "runs").glob("*.jsonl"))
            self.assertEqual(trace.read_text(encoding="utf-8"), output.getvalue())

        self.assertEqual(status, 0)
        docker.assert_not_called()
        self.assertEqual(create.call_args.kwargs["messages"][-1]["content"], "Build a summary.")
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([event["seq"] for event in events], [1, 2, 3, 4])
        self.assertEqual(events[-1]["data"]["text"], "Done.")

    def test_mentions_preserve_quoted_filenames_and_email_addresses(self):
        prompt, paths = file_mentions('读取 @"./inputs/sales sheet.csv" 并给 a@b.example 写摘要。', Path("/project"))
        self.assertEqual(paths, [Path("/project/inputs/sales sheet.csv")])
        self.assertIn('"sales sheet.csv"', prompt)
        self.assertIn("a@b.example", prompt)

    @unittest.skipUnless(os.name == "posix", "Uses the POSIX workspace worker.")
    def test_resume_restores_the_previous_result_in_a_new_workspace(self):
        create = AsyncMock(side_effect=[
            tool(1, "read_file", path="sales data.csv"),
            tool(2, "write_file", path="out/note.txt", content="first revision"),
            tool(3, "submit_results", paths=["out/note.txt"]),
            response("Delivered revision one."),
            tool(4, "read_file", path="out/note.txt"),
            tool(5, "write_file", path="out/note.txt", content="second revision", overwrite=True),
            tool(6, "submit_results", paths=["out/note.txt"]),
            response("Delivered revision two."),
        ])
        with (
            TemporaryDirectory() as directory,
            patch.dict(os.environ, ENV),
            patch.dict(sys.modules, {"openai": sdk(create)}),
            redirect_stdout(io.StringIO()) as output,
            redirect_stderr(io.StringIO()) as diagnostics,
        ):
            root = Path(directory)
            source = root / "sales data.csv"
            source.write_text("value\n7\n", encoding="utf-8")
            initial_root = root / "first"
            initial_root.mkdir()
            resumed_root = root / "second"
            resumed_root.mkdir()
            first = WorkerFixture(initial_root)
            second = WorkerFixture(resumed_root)
            with (
                patch("agent_cook.cli.DockerSandboxFactory", side_effect=[first, second]),
                patch("agent_cook.cli.prepare_docker"),
            ):
                with patch("sys.stdin", io.StringIO('Read @"sales data.csv" and write a note.\n/exit\n')):
                    status = main(["-C", directory])
                self.assertEqual(status, 0, diagnostics.getvalue())
                source.unlink()
                with patch("sys.stdin", io.StringIO("Revise the previous note.\n/exit\n")):
                    status = main(["resume", "--last", "-C", directory])
                self.assertEqual(status, 0, diagnostics.getvalue())

            state = LocalSessions(root / ".agent-cook" / "sessions.sqlite3").load(None)
            self.assertEqual(state.revision, 2)
            self.assertEqual(state.inputs["sales data.csv"], b"value\n7\n")
            self.assertEqual(state.results["out/note.txt"], b"second revision")
            self.assertEqual((first.closed, second.closed), (1, 1))
            self.assertIn("Delivered revision two.", output.getvalue())

        follow_up = create.call_args_list[4].kwargs["messages"]
        self.assertTrue(any(message["content"] == "Delivered revision one." for message in follow_up))
        read_back = create.call_args_list[5].kwargs["messages"][-1]["content"]
        self.assertIn("first revision", read_back)
