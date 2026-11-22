import asyncio
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from agent_cook.cli import main
from agent_cook.sandbox.docker import DockerSandboxFactory
from agent_cook.settings import DATA
from test_cli_entry import ENV, sdk, tool
from test_openrouter import response

IMAGE = os.environ.get("AGENT_COOK_TEST_DOCKER_IMAGE")
FIXTURE = "资料 样例"
SALES = f"{FIXTURE}/表格/销售 数据.csv"


@unittest.skipUnless(IMAGE, "Set AGENT_COOK_TEST_DOCKER_IMAGE to a locally built workspace image.")
class DockerDeliveryTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.workspace = Path(self.enterContext(TemporaryDirectory())).resolve()
        copytree(DATA / FIXTURE, self.directory / FIXTURE)
        self.original = (self.directory / SALES).read_bytes()
        self.diagnostics = self.enterContext(redirect_stderr(io.StringIO()))
        self.enterContext(redirect_stdout(io.StringIO()))
        self.enterContext(patch.dict(os.environ, {**ENV, "AGENT_COOK_WORKSPACE": str(self.workspace)}))

    def run_task(self, *turns, interactive=False):
        create = AsyncMock(side_effect=turns)
        with (
            patch.dict(sys.modules, {"openai": sdk(create)}),
            patch("agent_cook.cli.NativeSandboxFactory", return_value=DockerSandboxFactory(IMAGE)),
        ):
            if interactive:
                with patch("sys.stdin", io.StringIO(f'Update @"{FIXTURE}".\n/open\n/exit\n')):
                    return main(["-C", str(self.directory)])
            return main([
                "exec", "-C", str(self.directory),
                "--input", FIXTURE, "Update the copied table and deliver it with a summary.",
            ])

    def test_folder_round_trip_keeps_the_latest_versions_and_original(self):
        with patch("agent_cook.cli._open_directory") as open_results:
            status = self.run_task(
                tool(1, "read_file", path=SALES),
                tool(2, "write_file", path=SALES, content="draft"),
                tool(3, "submit_results", paths=[SALES]),
                tool(4, "write_file", path=SALES, content="final"),
                tool(5, "write_file", path=f"{FIXTURE}/总结.txt", content="Total units: 62"),
                tool(6, "submit_results", paths=[SALES, f"{FIXTURE}/总结.txt"]),
                response("Delivered."),
                interactive=True,
            )
        self.assertEqual(status, 0, self.diagnostics.getvalue())
        self.assertEqual((self.directory / SALES).read_bytes(), self.original)
        delivered, = self.workspace.glob("*/*/*-results")
        runs = delivered.parent
        self.assertFalse((self.directory / ".agent-cook").exists())
        open_results.assert_called_once_with(delivered)
        self.assertIn(str(delivered), self.diagnostics.getvalue())
        self.assertEqual((delivered / SALES).read_text(encoding="utf-8"), "final")
        self.assertEqual((delivered / FIXTURE / "总结.txt").read_text(encoding="utf-8"), "Total units: 62")
        self.assertEqual(len(list(delivered.glob("**/*.*"))), 2)
        self.assertEqual(len(list(runs.glob(f"*-artifacts/*/{SALES}"))), 2)

    def test_no_delivery_on_failure_or_cancellation_after_submission(self):
        for interruption, event, status in (
            (RuntimeError("provider failed"), "run.failed", 1),
            (asyncio.CancelledError(), "run.cancelled", 130),
        ):
            with self.subTest(event=event):
                result = self.run_task(
                    tool(1, "write_file", path=SALES, content="changed copy"),
                    tool(2, "submit_results", paths=[SALES]),
                    interruption,
                )
                self.assertEqual(result, status, self.diagnostics.getvalue())
        self.assertFalse(list(self.workspace.glob("*/*/*-results")))
        self.assertNotIn("Results:", self.diagnostics.getvalue())
        self.assertEqual((self.directory / SALES).read_bytes(), self.original)
        last_events = {
            json.loads(trace.read_text(encoding="utf-8").splitlines()[-1])["type"]
            for trace in self.workspace.glob("*/*/*.jsonl")
        }
        self.assertEqual(last_events, {"run.failed", "run.cancelled"})
        self.assertEqual(len(list(self.workspace.glob(f"*/*/*-artifacts/*/{SALES}"))), 2)
