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
from agent_cook.sandbox.interface import SandboxError
from test_openrouter import response, sdk_client
from test_workspace_tools import WorkerFixture


@unittest.skipUnless(os.name == "posix", "Skill file I/O uses POSIX directory descriptors.")
class SkillTest(unittest.TestCase):
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

    def test_cli_uses_real_workspace_paths_for_skill_files(self):
        create = AsyncMock()
        results = []
        workspace = self.root / "workspace"
        workspace.mkdir()
        worker = WorkerFixture(workspace)
        script = self.package / "scripts" / "show.py"
        script.parent.mkdir()
        script.write_text(
            "from pathlib import Path\n"
            "note = Path(__file__).parent.parent / 'references' / 'note.md'\n"
            "print(note.read_text().strip())\n",
            encoding="utf-8",
        )

        async def next_turn(**request):
            latest = request["messages"][-1]
            if latest["role"] == "user":
                self.assertEqual(list(workspace.iterdir()), [])
                self.entry.write_text(self.header + "CURRENT_BODY\n", encoding="utf-8")
                calls = [SimpleNamespace(
                    id=tool, type="function",
                    function=SimpleNamespace(name=tool, arguments=json.dumps(arguments)),
                ) for tool, arguments in (
                    ("ls", {"directory": "example", "recursive": True}),
                    ("read_file", {"path": "example/SKILL.md", "start_line": 6}),
                    ("copy_file", {"source": "example/references/note.md", "destination": "copied.md"}),
                    ("run_command", {"argv": [sys.executable, "example/scripts/show.py"]}),
                )]
                return response(calls=calls, finish_reason="tool_calls")
            results.extend(json.loads(message["content"]) for message in request["messages"] if message["role"] == "tool")
            return response("Read the skill.")

        create.side_effect = next_turn

        @asynccontextmanager
        async def open_client(**_):
            yield sdk_client(create)

        with (
            patch.dict(os.environ, {
                "OPENROUTER_API_KEY": "test-key", "OPENROUTER_MODEL": "fixture/model",
                "OPENROUTER_IMAGE_MODEL": "",
            }),
            patch.dict(sys.modules, {"openai": SimpleNamespace(AsyncOpenAI=open_client)}),
            patch("agent_cook.cli.DockerSandboxFactory", return_value=worker),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()) as stderr,
        ):
            status = main([
                "--sandbox", "docker", "--trace", str(self.root / "trace.jsonl"),
                "--skills-dir", str(self.skills), "Read the example skill",
            ])

        self.assertEqual(status, 0, stderr.getvalue())
        first_request = create.call_args_list[0].kwargs
        system = first_request["messages"][0]["content"]
        self.assertEqual(json.loads(system.splitlines()[-1]), [{
            "name": "example", "description": "Inspect a local example.", "entry": "example/SKILL.md",
        }])
        self.assertNotIn("INITIAL_BODY", system)
        self.assertNotIn("REFERENCE_BODY", system)
        self.assertNotIn(str(self.skills), system)
        self.assertEqual([entry["path"] for entry in results[0]["entries"]], [
            "example/SKILL.md", "example/references", "example/references/note.md",
            "example/scripts", "example/scripts/show.py",
        ])
        self.assertEqual(results[1]["content"], "CURRENT_BODY\n")
        self.assertEqual((workspace / "copied.md").read_text(encoding="utf-8"), "REFERENCE_BODY\n")
        self.assertEqual(results[3]["exit_code"], 0)
        self.assertEqual(results[3]["stdout"], "REFERENCE_BODY\n")
        self.assertEqual(worker.closed, 1)

    def test_skill_transfer_respects_package_boundaries_and_input_limits(self):
        from agent_cook.extensions.skills import SkillCatalog

        outside = self.root / "outside.txt"
        outside.write_text("outside the configured package", encoding="utf-8")
        (self.package / "outside.txt").symlink_to(outside)
        (self.package / "linked-dir").symlink_to(self.root, target_is_directory=True)
        catalog = SkillCatalog([self.skills])
        self.assertEqual(catalog.workspace_files({"input.txt": b"original"}), {
            "input.txt": b"original",
            "example/SKILL.md": (self.header + "INITIAL_BODY\n").encode(),
            "example/references/note.md": b"REFERENCE_BODY\n",
        })
        with self.assertRaisesRegex(SandboxError, "conflicts"):
            catalog.workspace_files({"example": b"already here"})
        with patch("agent_cook.extensions.files.MAX_TRANSFER_BYTES", len(self.entry.read_bytes())):
            with self.assertRaisesRegex(SandboxError, "limit"):
                catalog.workspace_files({"input.txt": b"original"})
