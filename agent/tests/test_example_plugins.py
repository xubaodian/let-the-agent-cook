import io
import json
import os
import shutil
import socket
import subprocess
import sys
import unittest
from contextlib import ExitStack, asynccontextmanager, contextmanager, redirect_stderr, redirect_stdout
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent_cook.cli import main
from agent_cook.context.prompts import CHECKPOINT_HEADINGS, CHECKPOINT_PROMPT
from agent_cook.extensions.plugins import load_plugins
from agent_cook.extensions.skills import SkillCatalog
from agent_cook.protocol.request import RunRequest
from example_browser_fixture import ExampleWorker
from test_openrouter import response, sdk_client

ROOT = Path(__file__).resolve().parents[2]
DATA_REVIEW = "Review the source rows and computed metrics."
BROWSER_REVIEW = "Verify the current registration page and refresh its PNG."
REPORT = ["out/report/metrics.json", "out/report/review.pptx"]
SHOP_FILES = [*REPORT, "out/report/analysis.xlsx", "out/report/brief.md"]
REGISTRATION_FILES = [f"out/site/{name}" for name in ("index.html", "style.css", "event.json", "serve.py", "preview.png")]
DASHBOARD_FILES = [f"out/site/{name}" for name in ("index.html", "metrics.json", "reference.json", "preview.png")]


def _tool(call_id, name, **arguments):
    return SimpleNamespace(
        id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


def _turn(*calls, prompt_tokens=None):
    return response(calls=calls, finish_reason="tool_calls", prompt_tokens=prompt_tokens)


def _json_file(call_id, path, document):
    return _tool(call_id, "write_file", path=path, content=json.dumps(document, ensure_ascii=False))


def _available_port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


@contextmanager
def _reference_plugin(root: Path, source: Path):
    port = _available_port()
    plugin = root / source.name
    shutil.copytree(source, plugin)
    config_path = plugin / "mcp.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["mcpServers"]["reference-library"]["url"] = f"http://127.0.0.1:{port}/mcp"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with (root / "reference-server.log").open("w+", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, str(plugin / "mcp/server.py"), "--port", str(port)],
            stdout=log, stderr=log,
        )
        try:
            deadline = monotonic() + 10
            while process.poll() is None and monotonic() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    sleep(0.05)
            else:
                log.seek(0)
                raise AssertionError(log.read() or "The reference MCP server did not start.")
            yield plugin
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def _file_paths(trace: Path) -> tuple[dict[str, Path], list[dict]]:
    events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    files = {}
    for event in events:
        if event["type"] == "artifact.created":
            path = Path(event["data"]["artifact"]["path"])
            files[Path(*path.parts[2:]).as_posix()] = trace.parent / path
    return files, events


@unittest.skipUnless(os.name == "posix", "The example workspace worker needs POSIX.")
class ExamplePluginsTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_catalog_discovers_bundled_skills_without_package_collisions(self):
        request = RunRequest.from_file(ROOT / "examples/combined-review/run.json")
        plugins = load_plugins(request.plugins)
        catalog = SkillCatalog((*request.skill_directories, *(plugin.skills_dir for plugin in plugins)))
        self.assertEqual(set(catalog.names), {
            "data-analysis", "research-synthesis", "i18n-review",
            "spreadsheet", "brief", "pptx", "static-site", "browser-qa", "image-assets",
        })
        staged = catalog.workspace_files({}, extra_trees=tuple(tree for plugin in plugins for tree in plugin.files))
        self.assertTrue(all(f"{name}/SKILL.md" in staged for name in catalog.names))
        self.assertIn("assets/reporting/build_review.py", staged)
        self.assertIn("assets/web/registration/index.html", staged)
        self.assertIn("hooks/reporting_submission.py", staged)
        self.assertIn("hooks/web_preview.py", staged)

    def test_weekly_review_reconciles_three_formats_and_rejects_incomplete_batch(self):
        self._require_office_tools()
        files, events, _ = self._run_case("shop-review", self._shop(), {DATA_REVIEW: self._data_review()})
        self.assertEqual(set(files), set(SHOP_FILES))
        self.assertIn("US$550,00", files["out/report/brief.md"].read_text(encoding="utf-8"))
        self.assertIn("Benchmark industri kategori Rumah dan Hunian", files["out/report/brief.md"].read_text(encoding="utf-8"))
        self._check_report(files)
        child = next(event for event in events if event["data"].get("agent") == "data-reviewer")
        self.assertEqual(child["parent_run_id"], events[0]["run_id"])
        self._assert_block(events, "reporting-studio", "incomplete")

    def test_registration_verifies_api_flows_and_requires_a_fresh_screenshot(self):
        browser = self._browser_executable()
        port = _available_port()
        files, events, workspace = self._run_case(
            "event-registration", self._registration(port),
            {BROWSER_REVIEW: self._browser_review(port)}, browser=browser,
        )
        self.assertEqual(set(files), set(REGISTRATION_FILES))
        copy = json.loads(files["out/site/event.json"].read_text(encoding="utf-8"))
        self.assertIn("18 October", copy["rules"])
        self.assertIn("#184b3b", files["out/site/style.css"].read_text(encoding="utf-8"))
        self.assertTrue(files["out/site/preview.png"].read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        proof = json.loads((workspace / "qa/web-preview.json").read_text(encoding="utf-8"))
        self.assertEqual(proof["tool_call_id"], "current-preview")
        child = next(event for event in events if event["data"].get("agent") == "browser-reviewer")
        self.assertEqual(child["parent_run_id"], events[0]["run_id"])
        self._assert_block(events, "web-studio", "stale")

    def test_two_plugins_share_metrics_and_resume_after_compaction(self):
        self._require_office_tools()
        files, events, _ = self._run_case("combined-review", self._combined(), browser=self._browser_executable())
        self.assertEqual(set(files), set([*REPORT, *DASHBOARD_FILES]))
        self._check_report(files)
        self.assertEqual(files[REPORT[0]].read_bytes(), files["out/site/metrics.json"].read_bytes())
        self.assertEqual(events[0]["data"]["mcp_tools"], [
            "mcp__fixture-data__query_rows", "mcp__reference-library__search",
        ])
        self.assertEqual(sum(event["type"] == "context.compacted" for event in events), 1)
        checked = {
            event["data"]["plugin"] for event in events if event["type"] == "hook.finished"
            and event["data"]["tool_call_id"] == "submit" and not event["data"]["is_error"]
        }
        self.assertEqual(checked, {"reporting-studio", "web-studio"})

    def _run_case(self, name, script, delegates=None, *, browser=None):
        if find_spec("mcp") is None:
            self.skipTest("Install the mcp extra to run the fixture servers.")
        source = ROOT / "examples" / name / "run.json"
        request = RunRequest.from_file(source)
        scripts = {request.task: script, **(delegates or {})}
        started = set()
        errors = []
        trace = self.root / "trace.jsonl"
        stderr_path = self.root / "stderr.log"
        workspace = self.root / "workspace"
        workspace.mkdir()
        worker = ExampleWorker(workspace, real_browser=browser is not None)

        async def next_turn(**payload):
            messages = payload["messages"]
            system = messages[0]["content"]
            try:
                if system == CHECKPOINT_PROMPT:
                    self.assertEqual(name, "combined-review")
                    history = json.loads(messages[1]["content"])["completed_history"]
                    self.assertIn("mcp__fixture-data__query_rows", json.dumps(history))
                    return response("\n\n".join(
                        f"## {heading}\n{body}" for heading, body in zip(CHECKPOINT_HEADINGS, (
                            "Build the weekly deck and website from the saved source rows.",
                            "Fetched the selected pages and metric reference.",
                            "out/report/metrics.json and out/site/metrics.json",
                            "The report was generated. Check the site in Playwright.",
                            "The site files are in the workspace.",
                            "Verify the filter and submit both sets of artifacts.",
                        ), strict=True)
                    ))
                task = messages[1]["content"]
                if task == request.task:
                    visible = {tool["function"]["name"] for tool in payload["tools"]}
                    expected_mcp = {f"mcp__{item.replace('/', '__')}" for item in request.mcp_tools}
                    self.assertEqual({name for name in visible if name.startswith("mcp__")}, expected_mcp)
                else:
                    allowed = {"ls", "read_file", "search_files"} if task == DATA_REVIEW else {"ls", "read_file", "playwright"}
                    self.assertEqual({tool["function"]["name"] for tool in payload["tools"]}, allowed)

                if task not in started:
                    started.add(task)
                    return next(scripts[task])
                observations = {}
                for message in messages:
                    if message["role"] == "tool":
                        content = message["content"]
                        try:
                            observations[message["tool_call_id"]] = json.loads(content)
                        except json.JSONDecodeError:
                            observations[message["tool_call_id"]] = content
                if name == "combined-review" and "dashboard-preview" in observations:
                    self.assertTrue(any("Runtime checkpoint from earlier turns" in (item["content"] or "") for item in messages))
                return scripts[task].send(observations)
            except StopIteration as done:
                return response(done.value)
            except Exception as error:
                errors.append(error)
                raise

        client = sdk_client(AsyncMock(side_effect=next_turn))

        @asynccontextmanager
        async def open_client(**_):
            yield client

        environment = {
            "OPENROUTER_API_KEY": "fixture-key", "OPENROUTER_MODEL": "fixture/model", "OPENROUTER_IMAGE_MODEL": "",
        }
        if browser:
            environment["AGENT_COOK_CHROMIUM"] = browser
        with ExitStack() as resources:
            if "reference-library/search" in request.mcp_tools:
                environment["ALL_PROXY"] = "socks5h://127.0.0.1:9"
                document = json.loads(source.read_text(encoding="utf-8"))
                document["skill_directories"] = [str(path.resolve()) for path in request.skill_directories]
                document["plugins"] = [
                    str(resources.enter_context(_reference_plugin(self.root, path.resolve())))
                    if path.name == "web-studio" else str(path.resolve())
                    for path in request.plugins
                ]
                source = self.root / "request.json"
                source.write_text(json.dumps(document), encoding="utf-8")
            resources.enter_context(patch.dict(os.environ, environment))
            resources.enter_context(patch.dict(sys.modules, {"openai": SimpleNamespace(AsyncOpenAI=open_client)}))
            resources.enter_context(patch("agent_cook.cli.NativeSandboxFactory", return_value=worker))
            resources.enter_context(redirect_stdout(io.StringIO()))
            stderr = resources.enter_context(stderr_path.open("w", encoding="utf-8"))
            resources.enter_context(redirect_stderr(stderr))
            status = main(["--sandbox", "native", "--trace", str(trace), "--request", str(source), "--max-turns", "24"])
        if errors:
            raise errors[0]
        self.assertEqual(status, 0, stderr_path.read_text(encoding="utf-8"))
        self.assertEqual(worker.closed, 1)
        files, events = _file_paths(trace)
        self.assertEqual(events[-1]["type"], "run.completed")
        return files, events, workspace

    def _rows(self, *, describe=False, reference=False):
        query = {"start_date": "2026-10-05", "end_date": "2026-10-11", "market": "ID", "page_size": 4}
        tools = [
            _tool("load-skill", "read_file", path="data-analysis/SKILL.md"),
            _tool("sales", "mcp__fixture-data__query_rows", dataset="sales", **query),
            _tool("benchmarks", "mcp__fixture-data__query_rows", dataset="category_benchmarks", **query),
        ]
        if describe:
            tools.append(_tool("describe", "mcp__fixture-data__describe_dataset", dataset="sales"))
        if reference:
            tools.append(_tool("method", "mcp__reference-library__search", query="GMV benchmark", locale="id-ID"))
        first = yield _turn(*tools)
        self.assertIn("# Data analysis", first["load-skill"]["content"])
        if describe:
            self.assertIn("USD cents", first["describe"]["gmv_cents"])
        if reference:
            self.assertEqual(first["method"]["hits"][0]["id"], "review-metrics")
        self.assertEqual(first["sales"]["next_cursor"], 4)
        self.assertIsNone(first["benchmarks"]["next_cursor"])
        second = yield _turn(_tool("page-two", "mcp__fixture-data__query_rows", dataset="sales", cursor=4, **query))
        self.assertIsNone(second["page-two"]["next_cursor"])
        return [*first["sales"]["rows"], *second["page-two"]["rows"]], first["benchmarks"]["rows"], first.get("method")

    def _build(self, sales, benchmarks, *, deck_only=False):
        command = [sys.executable, "assets/reporting/build_review.py", "--sales", "inputs/sales.json",
                   "--benchmarks", "inputs/benchmarks.json"]
        if deck_only:
            command.append("--deck-only")
        result = yield _turn(
            _json_file("save-sales", "inputs/sales.json", sales),
            _json_file("save-benchmarks", "inputs/benchmarks.json", benchmarks),
            _tool("build", "run_command", argv=command),
        )
        self.assertEqual(result["build"]["exit_code"], 0, result["build"])

    def _shop(self):
        sales, benchmarks, _ = yield from self._rows(describe=True)
        yield from self._build(sales, benchmarks)
        result = yield _turn(
            _tool("delegate-data", "delegate", agent="data-reviewer", task=DATA_REVIEW),
            _tool("incomplete", "submit_results", paths=REPORT),
        )
        self.assertIn("550 USD", result["delegate-data"])
        self.assertIn("complete report batch", result["incomplete"])
        result = yield _turn(_tool("submit", "submit_results", paths=SHOP_FILES))
        self.assertEqual(len(result["submit"]["artifacts"]), len(SHOP_FILES))
        return "The weekly review was submitted."

    def _data_review(self):
        result = yield _turn(
            _tool("read-orders", "read_file", path="inputs/sales.json"),
            _tool("read-metrics", "read_file", path=REPORT[0]),
        )
        rows = json.loads(result["read-orders"]["content"])
        self.assertEqual(len(rows), 7)
        by_order = {row["order_id"]: row["gmv_cents"] for row in rows}
        total = sum(amount for amount in by_order.values() if amount is not None)
        self.assertEqual(json.loads(result["read-metrics"]["content"])["gmv_cents"], total)
        return f"The recorded total is {total / 100:g} USD after deduplication."

    def _registration(self, port):
        result = yield _turn(
            _tool("rules", "mcp__reference-library__search", query="registration", locale="en", include_archived=True),
            _tool("browser-skill", "read_file", path="browser-qa/SKILL.md"),
        )
        self.assertIn("# Browser QA", result["browser-skill"]["content"])
        hits = result["rules"]["hits"]
        self.assertEqual({hit["version"] for hit in hits if hit["id"] == "registration-rules"}, {1, 2})
        rules = next(hit for hit in hits if hit["id"] == "registration-rules" and hit["current"])
        faq = next(hit for hit in hits if hit["id"] == "registration-faq")
        yield _turn(
            *(_tool(f"copy-{name}", "copy_file", source=f"assets/web/registration/{name}", destination=f"out/site/{name}")
              for name in ("index.html", "style.css", "serve.py")),
            _json_file("page-copy", "out/site/event.json", {
                "rules": rules["excerpt"], "faq": faq["excerpt"], "sources": [rules, faq],
            }),
        )
        result = yield _turn(self._registration_preview("old-preview", port))
        self.assertIsInstance(result["old-preview"], dict, result["old-preview"])
        self.assertEqual(result["old-preview"]["page_errors"], [])
        self.assertIn("18 October", result["old-preview"]["text"])
        result = yield _turn(
            _tool("change-style", "run_command", argv=[
                sys.executable, "-c",
                "from pathlib import Path; p = Path('out/site/style.css'); p.write_text(p.read_text() + '\\nbutton { background: #184b3b; }\\n')",
            ]),
            _tool("stale", "submit_results", paths=REGISTRATION_FILES),
        )
        self.assertIn("changed after browser verification", result["stale"])
        result = yield _turn(_tool("delegate-browser", "delegate", agent="browser-reviewer", task=BROWSER_REVIEW))
        self.assertIn("passed", result["delegate-browser"])
        result = yield _turn(_tool("submit", "submit_results", paths=REGISTRATION_FILES))
        self.assertEqual(len(result["submit"]["artifacts"]), len(REGISTRATION_FILES))
        return "The registration site was submitted."

    def _browser_review(self, port):
        result = yield _turn(self._registration_preview("current-preview", port))
        self.assertIsInstance(result["current-preview"], dict, result["current-preview"])
        self.assertEqual(result["current-preview"]["page_errors"], [])
        self.assertIn("Registration confirmed.", result["current-preview"]["text"])
        return "The empty, full, and successful form paths passed."

    @staticmethod
    def _registration_preview(call_id, port):
        return _tool(
            call_id, "playwright", cwd="out/site", url=f"http://127.0.0.1:{port}/",
            server_argv=[sys.executable, "serve.py", "--port", str(port)], screenshot="out/site/preview.png",
            actions=[
                {"action": "click", "selector": "button"},
                {"action": "expect_text", "selector": "#status", "value": "Enter your email."},
                {"action": "fill", "selector": "#email", "value": "capacity@example.test"},
                {"action": "click", "selector": "button"},
                {"action": "expect_text", "selector": "#status", "value": "The demo session is full."},
                {"action": "fill", "selector": "#email", "value": "guest@example.test"},
                {"action": "click", "selector": "button"},
                {"action": "expect_text", "selector": "#status", "value": "Registration confirmed."},
            ],
        )

    def _combined(self):
        sales, benchmarks, reference = yield from self._rows(reference=True)
        yield from self._build(sales, benchmarks, deck_only=True)
        yield _turn(
            _tool("dashboard", "copy_file", source="assets/web/dashboard/index.html", destination="out/site/index.html"),
            _tool("same-metrics", "copy_file", source=REPORT[0], destination="out/site/metrics.json"),
            _json_file("method-file", "out/site/reference.json", reference["hits"][0]),
            prompt_tokens=25_000,
        )
        result = yield _turn(_tool(
            "dashboard-preview", "playwright", path="out/site/index.html", screenshot="out/site/preview.png",
            actions=[
                {"action": "expect_text", "selector": "#amount", "value": "US$550,00"},
                {"action": "click", "selector": "[data-category=fashion]"},
                {"action": "expect_text", "selector": "#amount", "value": "US$190,00"},
            ],
        ))
        self.assertIsInstance(result["dashboard-preview"], dict, result["dashboard-preview"])
        self.assertIn("US$190,00", result["dashboard-preview"]["text"])
        self.assertEqual(result["dashboard-preview"]["page_errors"], [])
        result = yield _turn(_tool("submit", "submit_results", paths=[*REPORT, *DASHBOARD_FILES]))
        self.assertEqual(len(result["submit"]["artifacts"]), len(REPORT) + len(DASHBOARD_FILES))
        return "The combined review was submitted."

    def _check_report(self, files):
        from openpyxl import load_workbook
        from pptx import Presentation

        metrics = json.loads(files[REPORT[0]].read_text(encoding="utf-8"))
        self.assertEqual((metrics["orders"], metrics["valued_orders"], metrics["duplicate_rows"]), (6, 5, 1))
        self.assertEqual(metrics["gmv_cents"], 55_000)
        categories = {item["category"]: item for item in metrics["categories"]}
        self.assertEqual(categories["home-living"]["benchmark_cents"], 10_000)
        self.assertIsNone(categories["fashion"]["benchmark_cents"])
        deck = Presentation(files[REPORT[1]])
        self.assertTrue(any("US$550,00" in shape.text for shape in deck.slides[0].shapes if shape.has_text_frame))
        if "out/report/analysis.xlsx" in files:
            with files["out/report/analysis.xlsx"].open("rb") as stream:
                book = load_workbook(stream, read_only=True)
                try:
                    self.assertEqual(book["Metrics"]["B4"].value, 550)
                    self.assertEqual(book["Metrics"]["C4"].data_type, "f")
                finally:
                    book.close()

    def _assert_block(self, events, plugin, tool_call_id):
        self.assertTrue(any(
            event["type"] == "hook.finished" and event["data"]["plugin"] == plugin
            and event["data"]["tool_call_id"] == tool_call_id and event["data"]["is_error"]
            for event in events
        ))

    def _require_office_tools(self):
        if not all(find_spec(package) for package in ("openpyxl", "pptx")):
            self.skipTest("Install the examples extra to read and write the report artifacts.")

    def _browser_executable(self):
        if os.environ.get("AGENT_COOK_TEST_BROWSER") != "1":
            return None
        if find_spec("playwright") is None:
            self.skipTest("Install the examples extra for browser scenarios.")
        executable = os.environ.get("AGENT_COOK_CHROMIUM")
        if executable:
            return executable
        from playwright.sync_api import sync_playwright

        with sync_playwright() as browser:
            executable = browser.chromium.executable_path
        if Path(executable).is_file():
            return executable
        executable = shutil.which("chromium") or shutil.which("chromium-browser")
        if executable:
            return executable
        self.skipTest("Install Chromium for the browser scenarios.")
