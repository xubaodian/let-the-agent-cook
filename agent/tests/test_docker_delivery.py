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
        copytree(DATA / FIXTURE, self.directory / FIXTURE)
        self.original = (self.directory / SALES).read_bytes()
        self.diagnostics = self.enterContext(redirect_stderr(io.StringIO()))
        self.enterContext(redirect_stdout(io.StringIO()))
        self.enterContext(patch.dict(os.environ, ENV))

    def run_task(self, *turns):
        create = AsyncMock(side_effect=turns)
        with patch.dict(sys.modules, {"openai": sdk(create)}):
            return main([
                "exec", "-C", str(self.directory), "--docker-image", IMAGE,
                "--input", FIXTURE, "Update the copied table and deliver it with a summary.",
            ])

    def test_folder_round_trip_keeps_the_latest_versions_and_original(self):
        status = self.run_task(
            tool(1, "read_file", path=SALES),
            tool(2, "write_file", path=SALES, content="draft"),
            tool(3, "submit_results", paths=[SALES]),
            tool(4, "write_file", path=SALES, content="final"),
            tool(5, "write_file", path=f"{FIXTURE}/总结.txt", content="Total units: 62"),
            tool(6, "submit_results", paths=[SALES, f"{FIXTURE}/总结.txt"]),
            response("Delivered."),
        )
        self.assertEqual(status, 0, self.diagnostics.getvalue())
        self.assertEqual((self.directory / SALES).read_bytes(), self.original)
        runs = self.directory / ".agent-cook/runs"
        delivered, = runs.glob("*-results")
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
        runs = self.directory / ".agent-cook/runs"
        self.assertFalse(list(runs.glob("*-results")))
        self.assertNotIn("Results:", self.diagnostics.getvalue())
        self.assertEqual((self.directory / SALES).read_bytes(), self.original)
        last_events = {
            json.loads(trace.read_text(encoding="utf-8").splitlines()[-1])["type"] for trace in runs.glob("*.jsonl")
        }
        self.assertEqual(last_events, {"run.failed", "run.cancelled"})
        self.assertEqual(len(list(runs.glob(f"*-artifacts/*/{SALES}"))), 2)
