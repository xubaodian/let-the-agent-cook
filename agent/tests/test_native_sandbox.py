import asyncio
import io
import json
import os
import socket
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
from textwrap import dedent
from unittest.mock import AsyncMock, patch

from agent_cook.cli import main
from agent_cook.sandbox.native.factory import NativeSandboxFactory
from agent_cook.settings import DATA
from test_cli_entry import ENV, sdk, tool
from test_openrouter import response

_RUN_NATIVE = os.environ.get("AGENT_COOK_TEST_NATIVE") == "1"
_FOLDER = "资料 样例"
_SALES = f"{_FOLDER}/表格/销售 数据.csv"
_OFFICE = dedent("""
    import csv
    import sys
    from pathlib import Path
    from openpyxl import Workbook
    from pptx import Presentation

    source, label = map(Path, sys.argv[1:])
    rows = list(csv.reader(source.read_text(encoding="utf-8").splitlines()))
    book = Workbook()
    for row in rows:
        book.active.append(row)
    book.save(source.with_suffix(".xlsx"))
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[0])
    slide.shapes.title.text = label.read_text(encoding="utf-8")
    deck.save(source.with_suffix(".pptx"))
    source.write_text("modified copy", encoding="utf-8")
""")


@unittest.skipUnless(_RUN_NATIVE, "Set AGENT_COOK_TEST_NATIVE=1 on a host with native isolation.")
class NativeDeliveryTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory())).resolve()
        copytree(DATA / _FOLDER, self.root / _FOLDER)
        self.original = (self.root / _SALES).read_bytes()
        self.stderr = self.enterContext(redirect_stderr(io.StringIO()))
        self.enterContext(redirect_stdout(io.StringIO()))
        self.enterContext(patch.dict(os.environ, ENV))
        self.docker = self.enterContext(patch("agent_cook.cli.prepare_docker", side_effect=AssertionError))

    def run_task(self, turns, *, interactive=False):
        turns = iter(turns)
        self.observations = {}

        async def create(**options):
            self.observations.update(
                (message["tool_call_id"], message["content"])
                for message in options["messages"] if message["role"] == "tool"
            )
            turn = next(turns)
            if isinstance(turn, BaseException):
                raise turn
            return turn

        with patch.dict(sys.modules, {"openai": sdk(AsyncMock(side_effect=create))}):
            args = ["-C", str(self.root), "--timeout", "300", "--max-turns", "12"]
            if interactive:
                with patch("sys.stdin", io.StringIO(f'Process @"{_FOLDER}".\n/open\n/exit\n')):
                    return main(args)
            return main(["exec", *args, "--input", _FOLDER, "Process these files."])

    def test_interactive_folder_delivery_with_node_python_and_playwright(self):
        from openpyxl import load_workbook
        from pptx import Presentation

        node = (
            "const fs = require('node:fs');"
            "fs.writeFileSync('label.txt', 'Sales workbook');"
            "fs.writeFileSync('index.html', '<title>Sales</title><button onclick=\"this.textContent=\'Checked\'\">Run</button>');"
        )
        excel = _SALES.removesuffix(".csv") + ".xlsx"
        deck = _SALES.removesuffix(".csv") + ".pptx"
        with patch("agent_cook.cli._open_directory") as open_results:
            status = self.run_task([
                tool(1, "submit_results", paths=[_SALES]),
                tool(2, "run_command", argv=["node", "-e", node]),
                tool(3, "run_command", argv=["python", "-c", _OFFICE, _SALES, "label.txt"]),
                tool(4, "playwright", path="index.html", actions=[
                    {"action": "click", "selector": "button"},
                    {"action": "expect_text", "selector": "button", "value": "Checked"},
                ], screenshot="proof.png"),
                tool(5, "submit_results", paths=[_SALES, excel, deck, "proof.png"]),
                response("Delivered."),
            ], interactive=True)
        self.assertEqual(status, 0, self.stderr.getvalue())
        self.docker.assert_not_called()
        self.assertEqual((self.root / _SALES).read_bytes(), self.original)
        runs = self.root / ".agent-cook/runs"
        trace, = runs.glob("*.jsonl")
        events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[-1]["type"], "run.completed", events[-1])
        for event in events:
            if event["type"] == "tool.finished":
                call_id = event["data"]["tool_call_id"]
                self.assertFalse(event["data"]["is_error"], f"{call_id}: {self.observations.get(call_id)}")
        results, = runs.glob("*-results")
        open_results.assert_called_once_with(results)
        self.assertIn(str(results), self.stderr.getvalue())
        self.assertEqual((results / _SALES).read_text(encoding="utf-8"), "modified copy")
        self.assertEqual(len([file for file in results.rglob("*") if file.is_file()]), 4)
        sheet = load_workbook(results / excel)
        self.assertGreater(sheet.active.max_row, 1)
        sheet.close()
        self.assertEqual(Presentation(results / deck).slides[0].shapes.title.text, "Sales workbook")
        self.assertTrue((results / "proof.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_noninteractive_failure_and_cancellation_publish_no_directory(self):
        for error, status in ((RuntimeError("fixture failure"), 1), (asyncio.CancelledError(), 130)):
            self.assertEqual(self.run_task([
                tool(1, "write_file", path=_SALES, content="changed"),
                tool(2, "submit_results", paths=[_SALES]),
                error,
            ]), status)
        self.assertFalse(list((self.root / ".agent-cook/runs").glob("*-results")))
        self.assertEqual((self.root / _SALES).read_bytes(), self.original)


@unittest.skipUnless(_RUN_NATIVE, "Set AGENT_COOK_TEST_NATIVE=1 on a host with native isolation.")
class NativeBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_os_blocks_host_files_credentials_and_loopback(self):
        probe = dedent("""
            import os
            import socket
            import sys
            from pathlib import Path

            def denied(operation):
                try:
                    operation()
                except OSError:
                    return True
                return False

            outside = Path(sys.argv[1])
            assert denied(outside.read_bytes), "can read host file"
            assert denied(lambda: outside.write_text("changed")), "can write host file"
            assert denied(lambda: socket.create_connection(("127.0.0.1", int(sys.argv[2])), 1)), "can reach host"
            assert "OPENROUTER_API_KEY" not in os.environ
            assert Path("input.txt").read_text() == "allowed"
            Path("output.txt").write_text("ready")
        """)
        with TemporaryDirectory() as directory, socket.socket() as server:
            private = Path(directory).resolve() / "unrelated.txt"
            private.write_text("private fixture", encoding="utf-8")
            server.bind(("127.0.0.1", 0))
            server.listen()
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "fixture-only"}):
                async with NativeSandboxFactory().open("test") as sandbox:
                    await sandbox.probe()
                    await sandbox.put_file("input.txt", b"allowed")
                    result = await sandbox.exec(
                        ["python", "-c", probe, str(private), str(server.getsockname()[1])],
                        cwd=".", timeout_seconds=15,
                    )
                    self.assertEqual(result.exit_code, 0, result.stderr)
                    self.assertEqual(await sandbox.get_file("output.txt"), b"ready")
            self.assertEqual(private.read_text(encoding="utf-8"), "private fixture")

    async def test_cancelling_a_command_stops_its_child(self):
        child = "import time; from pathlib import Path; time.sleep(3); Path('late.txt').touch()"
        parent = (
            "import subprocess, sys, time; from pathlib import Path; "
            "subprocess.Popen([sys.executable, '-c', sys.argv[1]]); "
            "Path('started.txt').touch(); time.sleep(10)"
        )
        async with NativeSandboxFactory().open("cancel") as sandbox:
            running = asyncio.create_task(sandbox.exec(
                ["python", "-c", parent, child], cwd=".", timeout_seconds=12,
            ))
            try:
                async with asyncio.timeout(60):
                    while not (sandbox.workspace.root / "started.txt").exists():
                        if running.done():
                            self.fail(str(running.result()))
                        await asyncio.sleep(0.05)
            finally:
                running.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await running
            await asyncio.sleep(3)
            self.assertFalse((sandbox.workspace.root / "late.txt").exists())
