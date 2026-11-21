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
from agent_cook.protocol.request import RunRequest
from test_openrouter import response, sdk_client
from test_workspace_tools import WorkerFixture


def _call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


@unittest.skipUnless(os.name == "posix", "Plugin files use POSIX directory descriptors.")
class PluginTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.plugin = self.root / "plugins" / "reporting"
        self._write("plugin.json", json.dumps({
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "reporting",
            "version": "0.1.0",
        }))

    def _write(self, path, content):
        file = self.plugin / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")

    def test_run_request_selects_skills_and_loads_delegation_and_hooks(self):
        self._write("skills/brief/SKILL.md", "---\nname: brief\ndescription: Write a brief.\n---\nBRIEF_BODY\n")
        self._write("skills/unused/SKILL.md", "---\nname: unused\ndescription: Optional.\n---\n")
        self._write("assets/report.txt", "Ready template")
        self._write("mcp.json", json.dumps({
            "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
            "mcpServers": {"unused": {"type": "stdio", "command": "./unavailable-server"}},
        }))
        self._write(
            "agents/data-reviewer.md",
            "---\nname: data-reviewer\ndescription: Check report figures.\n"
            "tools: [read_file, delegate, unavailable]\n---\nREVIEWER_INSTRUCTIONS\n",
        )
        self._write("hooks/hooks.json", json.dumps({"hooks": [
            {"event": "before_tool", "tool": "submit_results", "script": "hooks/check.py"},
            {"event": "after_tool", "tool": "submit_results", "script": "hooks/record.py"},
        ]}))
        self._write(
            "hooks/check.py",
            "import json, sys\nfrom pathlib import Path\n"
            "call = json.load(sys.stdin)\n"
            "for path in call['arguments']['paths']:\n"
            "    if not Path(path).read_text().startswith('Ready'):\n"
            "        sys.exit('The brief is still a draft.')\n",
        )
        self._write(
            "hooks/record.py",
            "import json, sys\nfrom pathlib import Path\n"
            "Path('hook-result.json').write_text(json.dumps(json.load(sys.stdin)))\n"
            "sys.exit('The optional record hook failed.')\n",
        )
        request_path = self.root / "run.json"
        request_path.write_text(json.dumps({
            "schema_version": 1,
            "task": "Prepare a report.",
            "plugins": ["plugins/reporting"],
            "skills": ["brief"],
            "mcp_tools": [],
        }), encoding="utf-8")
        workspace = self.root / "workspace"
        workspace.mkdir()
        worker = WorkerFixture(workspace)
        observed = {}

        async def next_turn(**request):
            messages = request["messages"]
            last = messages[-1]
            if messages[0]["content"].endswith("\n\nREVIEWER_INSTRUCTIONS"):
                self.assertEqual([item["function"]["name"] for item in request["tools"]], ["read_file"])
                if last["role"] == "user":
                    self.assertEqual(len(messages), 2)
                    self.assertEqual(last["content"], "Check the brief instructions.")
                    return response(calls=[_call(
                        "child-read", "read_file", {"path": "brief/SKILL.md"},
                    )], finish_reason="tool_calls")
                self.assertIn("BRIEF_BODY", json.loads(last["content"])["content"])
                return response("Reviewed the brief instructions.")
            if last["role"] == "user":
                system = messages[0]["content"]
                self.assertEqual(json.loads(system.splitlines()[-1]), [{
                    "name": "brief", "description": "Write a brief.", "entry": "brief/SKILL.md",
                }])
                self.assertNotIn("REVIEWER_INSTRUCTIONS", system)
                delegate = next(tool["function"] for tool in request["tools"] if tool["function"]["name"] == "delegate")
                self.assertIn("Check report figures.", delegate["description"])
                return response(calls=[
                    _call("review", "delegate", {"agent": "data-reviewer", "task": "Check the brief instructions."}),
                    _call("draft", "write_file", {"path": "draft.md", "content": "Draft"}),
                    _call("blocked", "submit_results", {"paths": ["draft.md"]}),
                    _call("ready", "copy_file", {"source": "assets/report.txt", "destination": "brief.md"}),
                    _call("submit", "submit_results", {"paths": ["brief.md"]}),
                ], finish_reason="tool_calls")
            observed.update({
                message["tool_call_id"]: message["content"] for message in messages if message["role"] == "tool"
            })
            return response("Ready brief submitted.")

        @asynccontextmanager
        async def open_client(**_):
            yield sdk_client(AsyncMock(side_effect=next_turn))

        trace_path = self.root / "trace.jsonl"
        with (
            patch.dict(os.environ, {
                "OPENROUTER_API_KEY": "test-key", "OPENROUTER_MODEL": "fixture/model", "OPENROUTER_IMAGE_MODEL": "",
            }),
            patch.dict(sys.modules, {"openai": SimpleNamespace(AsyncOpenAI=open_client)}),
            patch("agent_cook.cli.NativeSandboxFactory", return_value=worker),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()) as stderr,
        ):
            status = main(["--sandbox", "native", "--request", str(request_path), "--trace", str(trace_path)])

        self.assertEqual(status, 0, stderr.getvalue())
        self.assertEqual(observed["review"], "Reviewed the brief instructions.")
        self.assertIn("blocked", observed["blocked"])
        self.assertIn("The brief is still a draft.", observed["blocked"])
        self.assertEqual(worker.closed, 1)
        self.assertFalse((workspace / "unused").exists())
        hook_result = json.loads((workspace / "hook-result.json").read_text(encoding="utf-8"))
        self.assertEqual(hook_result["tool_call_id"], "submit")
        self.assertFalse(hook_result["result"]["is_error"])
        trace = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(trace[0]["data"], {"plugins": ["reporting"], "skills": ["brief"], "mcp_tools": []})
        self.assertEqual([event["seq"] for event in trace], list(range(1, len(trace) + 1)))
        child = next(event for event in trace if event["data"].get("agent") == "data-reviewer")
        self.assertEqual(child["parent_run_id"], trace[0]["run_id"])
        self.assertNotEqual(child["run_id"], child["root_run_id"])
        failures = [event["data"]["tool_call_id"] for event in trace
                    if event["type"] == "hook.finished" and event["data"]["is_error"]]
        self.assertEqual(failures, ["blocked", "submit"])
        artifacts = [event for event in trace if event["type"] == "artifact.created"]
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["data"]["tool_call_id"], "submit")

    def test_explicit_empty_mount_and_unknown_skill(self):
        from agent_cook.extensions.skills import SkillCatalog

        self._write("skills/brief/SKILL.md", "---\nname: brief\ndescription: Write a brief.\n---\n")
        path = self.plugin / "skills"
        default = RunRequest.parse({"task": "Test."})
        empty = RunRequest.parse({"task": "Test.", "skills": [], "mcp_tools": []})
        canonical = RunRequest.parse({
            "task": "Test.", "skill_directories": ["skills"], "skills": ["brief"],
            "mcp_tools": ["project-data/lookup"],
        }, base=self.plugin)
        legacy = RunRequest.parse({
            "task": "Test.", "skills": {"directories": ["skills"], "mount": ["brief"]},
        }, base=self.plugin)
        self.assertEqual(SkillCatalog([path], default.mounted_skills).names, ("brief",))
        self.assertEqual(SkillCatalog([path], empty.mounted_skills).workspace_files({}), {})
        self.assertIsNone(default.mcp_tools)
        self.assertEqual(empty.mcp_tools, ())
        self.assertEqual(canonical.mcp_tools, ("project-data/lookup",))
        self.assertEqual(canonical.skill_directories, legacy.skill_directories)
        self.assertEqual(canonical.mounted_skills, legacy.mounted_skills)
        with self.assertRaisesRegex(ValueError, "Unknown mounted skills: absent"):
            SkillCatalog([path], ("absent",))
