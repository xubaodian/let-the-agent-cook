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
from agent_cook.tools.spec import ToolCall, ToolContext
from agent_cook.tools.workspace import workspace_tools
from test_openrouter import response, sdk_client


@unittest.skipUnless(os.name == "posix", "Skill file I/O uses POSIX directory descriptors.")
class SkillTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.skills = self.root / "skills"
        self.package = self.skills / "example"
        (self.package / "references").mkdir(parents=True)
        self.header = "---\nname: example\ndescription: >-\n  Inspect a local example.\n---\n"
        self.entry = self.package / "SKILL.md"
        self.entry.write_text(self.header + "INITIAL_BODY\n", encoding="utf-8")
        (self.package / "references" / "note.md").write_text("REFERENCE_BODY\n", encoding="utf-8")

    def test_cli_lists_metadata_and_fetches_the_skill_on_demand(self):
        create = AsyncMock()
        reads = []

        async def next_turn(**request):
            latest = request["messages"][-1]
            if latest["role"] == "user":
                self.entry.write_text(self.header + "CURRENT_BODY\n", encoding="utf-8")
                calls = [SimpleNamespace(
                    id=tool, type="function",
                    function=SimpleNamespace(name=tool, arguments=json.dumps(arguments)),
                ) for tool, arguments in (
                    ("ls", {"directory": "skill://example", "recursive": True}),
                    ("read_file", {"path": "skill://example/SKILL.md", "start_line": 6}),
                )]
                return response(calls=calls, finish_reason="tool_calls")
            reads.extend(json.loads(message["content"]) for message in request["messages"] if message["role"] == "tool")
            return response("Read the skill.")

        create.side_effect = next_turn

        @asynccontextmanager
        async def open_client(**_):
            yield sdk_client(create)

        with (
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key", "OPENROUTER_MODEL": "fixture/model"}),
            patch.dict(sys.modules, {"openai": SimpleNamespace(AsyncOpenAI=open_client)}),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()) as stderr,
        ):
            status = main([
                "--trace", str(self.root / "trace.jsonl"), "--skills-dir", str(self.skills), "Read the example skill",
            ])

        self.assertEqual(status, 0, stderr.getvalue())
        first_request = create.call_args_list[0].kwargs
        system = first_request["messages"][0]["content"]
        self.assertEqual(json.loads(system.splitlines()[-1]), [{
            "name": "example", "description": "Inspect a local example.", "entry": "skill://example/SKILL.md",
        }])
        self.assertNotIn("INITIAL_BODY", system)
        self.assertNotIn("REFERENCE_BODY", system)
        self.assertEqual([entry["path"] for entry in reads[0]["entries"]], [
            "skill://example/SKILL.md", "skill://example/references", "skill://example/references/note.md",
        ])
        self.assertEqual(reads[1]["content"], "CURRENT_BODY\n")

    async def test_skill_tools_stay_within_registered_packages(self):
        from agent_cook.extensions.skills import SkillCatalog

        outside = self.root / "outside.txt"
        outside.write_text("outside the configured package", encoding="utf-8")
        (self.package / "outside.txt").symlink_to(outside)
        registry, names = workspace_tools(None, skills=SkillCatalog([self.skills]))
        get_sandbox = AsyncMock()
        context = ToolContext("fixture", frozenset(names), get_sandbox)

        async def invoke(tool, **arguments):
            return await registry.invoke(ToolCall(tool, tool, json.dumps(arguments)), context)

        catalog = await invoke("ls", directory="skill://")
        self.assertEqual(json.loads(catalog.model_content)["entries"], [
            {"path": "skill://example", "kind": "directory"},
        ])
        note = await invoke("read_file", path="skill://example/references/note.md")
        self.assertEqual(json.loads(note.model_content)["content"], "REFERENCE_BODY\n")
        for path in ("skill://example/../outside.txt", "skill://example/outside.txt", "skill://unknown/SKILL.md"):
            with self.subTest(path=path):
                self.assertTrue((await invoke("read_file", path=path)).is_error)
        self.assertTrue((await invoke("read_file", path="ordinary.txt")).is_error)
        get_sandbox.assert_not_awaited()
