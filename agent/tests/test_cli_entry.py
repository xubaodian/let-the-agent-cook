import io
import json
import os
import sys
import unittest
from contextlib import asynccontextmanager, redirect_stderr, redirect_stdout
from datetime import date
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
    "OPENROUTER_IMAGE_MODEL": "", "AGENT_COOK_SANDBOX": "", "EXA_API_KEY": "",
}
BUILTIN_SKILLS = ["brief", "data-analysis", "image-assets", "pptx", "research-synthesis", "static-site"]


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
    def setUp(self):
        self.workspace = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, AGENT_COOK_WORKSPACE=str(self.workspace)))

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
            trace, = (self.workspace / date.today().isoformat()).glob("*/*.jsonl")
            self.assertEqual(trace.stem, trace.parent.name)
            self.assertFalse((Path(directory) / ".agent-cook").exists())
            self.assertEqual(trace.read_text(encoding="utf-8"), output.getvalue())

        self.assertEqual(status, 0)
        docker.assert_not_called()
        self.assertEqual(create.call_args.kwargs["messages"][-1]["content"], "Build a summary.")
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([event["seq"] for event in events], [1, 2, 3, 4, 5])
        self.assertEqual(events[0]["type"], "extensions.loaded")
        self.assertEqual(events[0]["data"]["plugins"], [])
        self.assertEqual(events[0]["data"]["skills"], BUILTIN_SKILLS)
        request = create.call_args.kwargs
        skill_entries = json.loads(request["messages"][0]["content"].splitlines()[-1])
        self.assertEqual([skill["name"] for skill in skill_entries], BUILTIN_SKILLS)
        self.assertTrue(all(set(skill) == {"name", "description", "entry"} for skill in skill_entries))
        self.assertNotIn("delegate", {tool["function"]["name"] for tool in request["tools"]})
        self.assertEqual(events[-1]["data"]["text"], "Done.")

    def test_enabling_a_bundled_studio_resolves_references_without_connecting_sample_mcp(self):
        for studio, skills in (
            ("reporting-studio", BUILTIN_SKILLS),
            ("project-reporting-studio", ["brief", "data-analysis", "image-assets", "pptx"]),
        ):
            with self.subTest(studio=studio), TemporaryDirectory() as directory:
                create = AsyncMock(return_value=response("Done."))
                output = io.StringIO()
                with (
                    patch.dict(os.environ, ENV),
                    patch.dict(sys.modules, {"openai": sdk(create)}),
                    patch("agent_cook.extensions.mcp._open_session", new_callable=AsyncMock) as connect,
                    redirect_stdout(output),
                    redirect_stderr(io.StringIO()),
                ):
                    status = main(["exec", "-C", directory, "--json", "--plugin", studio,
                                   "--skill", "image-assets", "Prepare a report."])
                self.assertEqual(status, 0)
                connect.assert_not_awaited()
                event = json.loads(output.getvalue().splitlines()[0])
                self.assertEqual(event["data"], {"plugins": [studio], "skills": skills, "mcp_tools": []})
                request = create.call_args.kwargs
                tools = {entry["function"]["name"] for entry in request["tools"]}
                self.assertIn("delegate", tools)
                self.assertIn(f"Plugin {studio}\n", request["messages"][0]["content"])

    @unittest.skipUnless(os.name == "posix", "Uses the POSIX workspace worker.")
    def test_bundled_skills_keep_conflicting_input_paths_readable(self):
        create = AsyncMock(side_effect=[
            tool(1, "read_file", path="Brief/notes.txt"),
            tool(2, "read_file", path="skills-2/brief/SKILL.md"),
            response("Read the source notes and the brief guide."),
        ])
        with (
            TemporaryDirectory() as directory,
            patch.dict(os.environ, ENV),
            patch.dict(sys.modules, {"openai": sdk(create)}),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()) as diagnostics,
        ):
            root = Path(directory)
            source = root / "Brief"
            source.mkdir()
            (source / "notes.txt").write_text("Source notes", encoding="utf-8")
            (root / "skills").write_text("User attachment", encoding="utf-8")
            workspace = root / "workspace"
            workspace.mkdir()
            with patch("agent_cook.cli.NativeSandboxFactory", return_value=WorkerFixture(workspace)):
                status = main(["exec", "-C", directory, "--input", "Brief", "--input", "skills", "Read the notes."])

            self.assertEqual(status, 0, diagnostics.getvalue())
            self.assertEqual((workspace / "Brief/notes.txt").read_text(encoding="utf-8"), "Source notes")
            self.assertEqual((workspace / "skills").read_text(encoding="utf-8"), "User attachment")
            messages = create.call_args_list[0].kwargs["messages"]
            entries = {item["name"]: item["entry"] for item in json.loads(messages[0]["content"].splitlines()[-1])}
            self.assertEqual(entries["brief"], "skills-2/brief/SKILL.md")
            self.assertTrue(all((workspace / path).is_file() for path in entries.values()))
            for call, expected in zip(create.call_args_list[1:], ["Source notes", "name: brief"], strict=True):
                result = json.loads(call.kwargs["messages"][-1]["content"])
                self.assertIn(expected, result["content"])

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
                patch("agent_cook.cli.NativeSandboxFactory", side_effect=[first, second]),
            ):
                with patch("sys.stdin", io.StringIO('Read @"sales data.csv" and write a note.\n/exit\n')):
                    status = main(["-C", directory])
                self.assertEqual(status, 0, diagnostics.getvalue())
                source.unlink()
                with patch("sys.stdin", io.StringIO("Revise the previous note.\n/exit\n")):
                    status = main(["resume", "--last", "-C", directory])
                self.assertEqual(status, 0, diagnostics.getvalue())

            state = LocalSessions.for_directory(self.workspace, root).load(None)
            session = self.workspace / state.created_on / state.id
            self.assertEqual(len(list(session.glob("*.jsonl"))), 2)
            self.assertEqual(len(list(session.glob("*-results"))), 2)
            self.assertEqual(Path(state.result_dir).parent, session)
            self.assertFalse((root / ".agent-cook").exists())
            self.assertEqual(state.revision, 2)
            self.assertEqual(state.inputs["sales data.csv"], b"value\n7\n")
            self.assertEqual(state.results["out/note.txt"], b"second revision")
            self.assertEqual((first.closed, second.closed), (1, 1))
            self.assertIn("Delivered revision two.", output.getvalue())

        follow_up = create.call_args_list[4].kwargs["messages"]
        self.assertTrue(any(message["content"] == "Delivered revision one." for message in follow_up))
        read_back = create.call_args_list[5].kwargs["messages"][-1]["content"]
        self.assertIn("first revision", read_back)
