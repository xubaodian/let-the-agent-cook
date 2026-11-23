import io
import json
import os
import socket
import sys
import unittest
from contextlib import asynccontextmanager, redirect_stderr, redirect_stdout
from http.client import HTTPConnection, HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch
from urllib.parse import urlsplit

from agent_cook.cli import _configure, _execute, _parser, _prepare, main
from agent_cook.model.openrouter import OpenRouterModel
from agent_cook.protocol.request import RunRequest
from agent_cook.sandbox.docker import DEFAULT_DOCKER_IMAGE
from agent_cook.sandbox.worker.browser_proxy import _connect_public, browser_proxy
from agent_cook.state.session import Message
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.spec import ToolCall, ToolContext, ToolDefinition
from agent_cook.tools.web import register_exa_tools
from test_openrouter import response, sdk_client
from test_openrouter_responses import function_item, reply, text_item
from test_workspace_tools import WorkerFixture

_ORIGIN = "https://docs.example.com"
_PAGE = _ORIGIN + "/pricing"
_SNAPSHOT = b"\x89PNG\r\n\x1a\npublic-browser-fixture"


class WebRequestTest(unittest.TestCase):
    def test_hosted_tools_and_public_browser_are_independent(self):
        default = RunRequest.parse({"task": "Check."})
        configured = RunRequest.parse({
            "task": "Check.",
            "web": {"browser_origins": ["https://DOCS.example.com:443/", "https://cdn.example.com:8443"]},
        })
        self.assertFalse(default.web.enabled)
        self.assertEqual(default.web.browser_origins, ())
        self.assertFalse(configured.web.enabled)
        self.assertEqual(configured.web.browser_origins, (_ORIGIN, "https://cdn.example.com:8443"))

    def test_browser_origins_reject_non_https_or_ambiguous_targets(self):
        for origin in (
            "http://example.com", "https://*.example.com", "https://localhost",
            "https://127.0.0.1", "https://[::1]", "https://example.com:0",
            "https://user@example.com", "https://example.com/path",
            "https://example.com?key=secret", "https://example.com/#section",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                RunRequest.parse({"task": "Check.", "web": {"browser_origins": [origin]}})
        with self.assertRaisesRegex(ValueError, "only once"):
            RunRequest.parse({"task": "Check.", "web": {"browser_origins": [_ORIGIN, _ORIGIN + ":443/"]}})


class OpenRouterWebTest(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(find_spec("openai"), "The OpenAI SDK is needed for the HTTP transport test.")
    async def test_hosted_tools_reach_the_wire_with_local_functions_and_report_usage(self):
        from openai import AsyncOpenAI

        sent: list[dict] = []
        payload = json.dumps({
            "id": "completion", "object": "chat.completion", "created": 0, "model": "fixture/model",
            "choices": [{
                "index": 0, "finish_reason": "stop",
                "message": {
                    "role": "assistant", "content": "Found it.",
                    "annotations": [{"type": "url_citation", "url_citation": {
                        "url": _PAGE, "title": "Pricing", "start_index": 0, "end_index": 8,
                    }}],
                },
            }],
            "usage": {
                "prompt_tokens": 100, "completion_tokens": 6, "total_tokens": 106,
                "server_tool_use_details": {"web_search_requests": 1, "tool_calls_executed": 2},
            },
        }).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                sent.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: object) -> None:
                return

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                async with AsyncOpenAI(
                    api_key="fixture-key", base_url=f"http://127.0.0.1:{server.server_port}/api/v1",
                    timeout=3, max_retries=0,
                ) as client:
                    model = OpenRouterModel(client)
                    local = ToolDefinition("playwright", "Open a page.", {"type": "object"})
                    messages = (Message("user", "Find pricing information."),)
                    turn = await model.next_turn(
                        model="fixture/model", messages=messages, tools=(local,), web_enabled=True,
                    )
                    await model.next_turn(model="fixture/model", messages=messages, tools=())
            finally:
                server.shutdown()
                thread.join(timeout=2)

        self.assertEqual([item["type"] for item in sent[0]["tools"]], [
            "function", "openrouter:web_search", "openrouter:web_fetch",
        ])
        self.assertEqual(sent[0]["tools"][0]["function"]["name"], "playwright")
        self.assertNotIn("tools", sent[1])
        self.assertEqual(turn.web_usage, {"web_search_requests": 1, "tool_calls_executed": 2})
        self.assertEqual(turn.web_sources, ({"url": _PAGE, "title": "Pricing"},))


class ExaWebTest(unittest.TestCase):
    def test_cli_calls_exa_mcp_and_returns_sources_to_the_model(self):
        import httpx

        query = "Apple FY2026 Q4 earnings results 大中华区"
        url = _PAGE + "/reports?quarter=FY2026-Q4&region=CN#results"
        mcp_calls = []

        def mcp_endpoint(request):
            self.assertEqual(request.url.host, "mcp.exa.ai")
            self.assertEqual(request.url.params["defaultSearchType"], "lite")
            self.assertEqual(request.url.params["tools"], "web_search_exa,web_fetch_exa")
            self.assertEqual(request.headers["x-api-key"], "fixture-exa-key")
            message = json.loads(request.content)
            if message["method"] == "initialize":
                result = {
                    "protocolVersion": message["params"]["protocolVersion"],
                    "capabilities": {"tools": {}}, "serverInfo": {"name": "exa", "version": "1"},
                }
            elif message["method"] == "tools/list":
                result = {"tools": [
                    {"name": name, "inputSchema": {"type": "object"}} for name in ("web_search_exa", "web_fetch_exa")
                ]}
            elif message["method"] == "tools/call":
                mcp_calls.append(message["params"])
                if message["params"]["name"] == "web_search_exa":
                    result = {"content": [], "structuredContent": {"sources": [{"url": url, "title": "Results"}]}}
                else:
                    result = {"content": [{"type": "text", "text": "Page text from Exa."}]}
            else:
                self.assertEqual(message["method"], "notifications/initialized")
                return httpx.Response(202)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

        def requested_tool(name, **arguments):
            function = SimpleNamespace(name=name, arguments=json.dumps(arguments))
            return response(
                calls=[SimpleNamespace(id=name, type="function", function=function)], finish_reason="tool_calls",
            )

        http_client = httpx.AsyncClient
        for api, history, content, turns in (
            ("chat_completions", "messages", "content", [
                requested_tool("web_search", query=query), requested_tool("web_fetch", url=url), response("Verified."),
            ]),
            ("responses", "input", "output", [
                reply(function_item("web_search", "search", query=query)),
                reply(function_item("web_fetch", "fetch", url=url)),
                reply(text_item("Verified.")),
            ]),
        ):
            create = AsyncMock(side_effect=turns)
            client = sdk_client(create)
            client.responses = SimpleNamespace(create=create)

            @asynccontextmanager
            async def open_client(**_):
                yield client

            with (
                self.subTest(api=api),
                TemporaryDirectory() as directory,
                patch.dict(os.environ, {
                    "OPENROUTER_API_KEY": "fixture-key", "OPENROUTER_MODEL": "fixture/model",
                }, clear=True),
                patch.dict(sys.modules, {"openai": SimpleNamespace(AsyncOpenAI=open_client)}),
                patch("httpx.AsyncClient", side_effect=lambda **kwargs: http_client(
                    transport=httpx.MockTransport(mcp_endpoint), **kwargs,
                )),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()) as diagnostics,
            ):
                config = Path(directory) / "agent-cook.json"
                config.write_text(json.dumps({"api_keys": {"exa": "fixture-exa-key"}}), encoding="utf-8")
                status = main([
                    "exec", "--sandbox", "none", "-C", directory, "--config", str(config),
                    "--model-api", api, "Find the earnings results.",
                ])
                self.assertEqual(status, 0, diagnostics.getvalue())
                first, second, third = (attempt.kwargs for attempt in create.await_args_list)
                tools = {item.get("function", item)["name"]: item.get("function", item) for item in first["tools"]}
                self.assertEqual(set(tools), {"web_search", "web_fetch"})
                for name, field in (("web_search", "query"), ("web_fetch", "url")):
                    self.assertNotIn("pattern", tools[name]["parameters"]["properties"][field])
                self.assertIn(url, second[history][-1][content])
                self.assertEqual(third[history][-1][content], "Page text from Exa.")
                self.assertNotIn("fixture-exa-key", json.dumps(third))
                self.assertEqual(mcp_calls, [
                    {"name": "web_search_exa", "arguments": {"query": query, "numResults": 5}},
                    {"name": "web_fetch_exa", "arguments": {"urls": [url], "maxCharacters": 4000}},
                ])
            mcp_calls.clear()

    def test_web_settings_choose_one_backend_without_contacting_exa_for_other_tasks(self):
        cases = (
            ("fixture-exa-key", None, ["function", "function"]),
            ("fixture-exa-key", True, ["function", "function"]),
            ("fixture-exa-key", False, []),
            ("", True, ["openrouter:web_search", "openrouter:web_fetch"]),
            ("", None, []),
        )
        for key, enabled, expected in cases:
            with self.subTest(exa_configured=bool(key), enabled=enabled), TemporaryDirectory() as directory:
                create = AsyncMock(return_value=response("Done."))

                @asynccontextmanager
                async def open_client(**_):
                    yield sdk_client(create)

                request = {"task": "Say hello."}
                if enabled is not None:
                    request["web"] = {"enabled": enabled}
                (Path(directory) / "request.json").write_text(json.dumps(request), encoding="utf-8")
                with (
                    patch.dict(os.environ, {
                        "OPENROUTER_API_KEY": "fixture-key", "OPENROUTER_MODEL": "fixture/model", "EXA_API_KEY": key,
                    }, clear=True),
                    patch.dict(sys.modules, {"openai": SimpleNamespace(AsyncOpenAI=open_client)}),
                    patch("agent_cook.tools.web.call_mcp_tool", new_callable=AsyncMock) as mcp,
                    redirect_stdout(io.StringIO()),
                    redirect_stderr(io.StringIO()),
                ):
                    self.assertEqual(main(["exec", "--sandbox", "none", "-C", directory, "--request", "request.json"]), 0)
                mcp.assert_not_awaited()
                request_sent = create.await_args.kwargs
                tools = request_sent.get("tools", request_sent.get("extra_body", {}).get("tools", []))
                self.assertEqual([item["type"] for item in tools], expected)


class ExaArgumentTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_arguments_are_reported_before_exa_is_called(self):
        registry = ToolRegistry()
        names = register_exa_tools(registry, "fixture-key")
        context = ToolContext("exa", frozenset(names), AsyncMock(side_effect=AssertionError))
        with patch("agent_cook.tools.web.call_mcp_tool", new_callable=AsyncMock) as call_exa:
            for name, field, value, message in (
                ("web_search", "query", " \t\n", "complete search query"),
                ("web_fetch", "url", "https://", "including its hostname"),
                ("web_fetch", "url", "ftp://example.com/path", "including its hostname"),
                ("web_fetch", "url", "https://example.com:65536", "including its hostname"),
            ):
                with self.subTest(name=name, value=value):
                    result = await registry.invoke(ToolCall("invalid", name, json.dumps({field: value})), context)
                    self.assertTrue(result.is_error)
                    self.assertIn(message, result.model_content)
        call_exa.assert_not_awaited()


class BrowserProxyTest(unittest.TestCase):
    def test_proxy_relays_traffic_to_the_approved_address(self):
        upstream, site = socket.socketpair()
        with upstream, site, browser_proxy(frozenset({_ORIGIN})) as endpoint, patch(
            "agent_cook.sandbox.worker.browser_proxy._connect_public", return_value=upstream,
        ) as connect:
            proxy = urlsplit(endpoint)
            site.settimeout(2)
            with socket.create_connection((proxy.hostname, proxy.port), timeout=2) as client:
                client.sendall(b"CONNECT docs.example.com:443 HTTP/1.1\r\nHost: docs.example.com:443\r\n\r\n")
                with HTTPResponse(client, method="CONNECT") as response:
                    response.begin()
                    self.assertEqual(response.status, 200)
                with site.makefile("rb") as site_reader, client.makefile("rb") as client_reader:
                    client.sendall(b"ping")
                    self.assertEqual(site_reader.read(4), b"ping")
                    site.sendall(b"pong")
                    self.assertEqual(client_reader.read(4), b"pong")
            connect.assert_called_once_with("docs.example.com", 443)

    def test_proxy_rejects_unlisted_hosts_and_ports_before_resolution(self):
        with browser_proxy(frozenset({_ORIGIN})) as endpoint, patch(
            "agent_cook.sandbox.worker.browser_proxy._connect_public",
        ) as connect:
            proxy = urlsplit(endpoint)
            for authority in ("other.example.com:443", "docs.example.com:444", "127.0.0.1:443"):
                with self.subTest(authority=authority):
                    connection = HTTPConnection(proxy.hostname, proxy.port, timeout=2)
                    try:
                        connection.request("CONNECT", authority)
                        self.assertEqual(connection.getresponse().status, 403)
                    finally:
                        connection.close()
            connect.assert_not_called()

    def test_proxy_pins_connection_to_a_resolved_public_address(self):
        records = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:192.168.1.10", 443, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
        ]
        upstream = MagicMock()
        with patch("socket.getaddrinfo", return_value=records), patch("socket.socket", return_value=upstream) as dial:
            self.assertIs(_connect_public("docs.example.com", 443), upstream)
            dial.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM, 6)
            upstream.connect.assert_called_once_with(("1.1.1.1", 443))
        with patch("socket.getaddrinfo", return_value=records[:2]), patch("socket.socket") as dial:
            with self.assertRaisesRegex(OSError, "public address"):
                _connect_public("docs.example.com", 443)
            dial.assert_not_called()


class _PublicWorker(WorkerFixture):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.previews = []

    async def exchange(self, request: bytes, timeout: float) -> bytes:
        payload = json.loads(request)
        if payload["operation"] == "download":
            return await super().exchange(request, timeout)
        if payload["operation"] != "playwright_public":
            raise AssertionError("The browser sandbox received a workspace operation.")
        arguments = payload["arguments"]
        self.previews.append(arguments)
        screenshot = arguments["screenshot"]
        if screenshot is not None:
            (self.root / screenshot).write_bytes(_SNAPSHOT)
        return json.dumps({
            "version": payload["version"], "ok": True,
            "result": {"url": _PAGE, "title": "Pricing", "text": "Fixture prices", "page_errors": [], "screenshot": screenshot},
        }).encode()


@unittest.skipUnless(os.name == "posix", "The workspace worker needs POSIX.")
class WebCliTest(unittest.TestCase):
    def test_public_playwright_isolated_from_workspace_and_records_provider_sources(self):
        def tool(id, name, **arguments):
            return SimpleNamespace(
                id=id, type="function", function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
            )

        first = response(calls=[
            tool("deny", "playwright", url="https://other.example.com/"),
            tool("deny-command", "playwright", url=_PAGE, server_argv=["python", "server.py"]),
            tool("read", "playwright", url=_PAGE, screenshot="proof.png"),
        ], finish_reason="tool_calls", prompt_tokens=120)
        first.usage.server_tool_use_details = {"web_search_requests": 1}
        first.choices[0].message.annotations = [
            {"type": "url_citation", "url_citation": {"url": _PAGE, "title": "Pricing"}},
        ]
        create = AsyncMock(side_effect=[
            first,
            response(calls=[tool("submit", "submit_results", paths=["proof.png"])], finish_reason="tool_calls"),
            response("Verified the pricing page."),
        ])

        @asynccontextmanager
        async def open_client(**_):
            yield sdk_client(create)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "workspace").mkdir()
            (root / "browser").mkdir()
            local = WorkerFixture(root / "workspace")
            browser = _PublicWorker(root / "browser")
            trace = root / "run.jsonl"
            old_proof = root / "proof.png"
            old_proof.write_bytes(b"old evidence")
            request = root / "request.json"
            request.write_text(json.dumps({
                "task": "Verify the pricing page and save evidence.",
                "web": {"enabled": True, "browser_origins": [_ORIGIN]},
            }), encoding="utf-8")
            with (
                patch.dict(os.environ, {
                    "OPENROUTER_API_KEY": "fixture-key", "OPENROUTER_MODEL": "fixture/model", "OPENROUTER_IMAGE_MODEL": "",
                }),
                patch.dict(sys.modules, {"openai": SimpleNamespace(AsyncOpenAI=open_client)}),
                patch("agent_cook.cli.DockerSandboxFactory", side_effect=[local, browser]) as factory,
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()) as stderr,
            ):
                args = _parser("serve").parse_args(["--sandbox", "docker", "--trace", str(trace)])
                catalog = _configure(args, root, "serve")
                job = RunRequest.from_file(request)
                prepared = _prepare(args, job, catalog)
                result, _, _ = _execute(args, prepared, job.task, "fixture-key", {"proof.png": old_proof.read_bytes()})
            self.assertEqual(result.text, "Verified the pricing page.", stderr.getvalue())
            self.assertEqual(factory.call_args_list, [
                call(DEFAULT_DOCKER_IMAGE, allow_network=False), call(DEFAULT_DOCKER_IMAGE, allow_network=True),
            ])
            self.assertEqual((local.closed, browser.closed), (1, 1))
            self.assertEqual(browser.previews, [{
                "url": _PAGE, "browser_origins": [_ORIGIN], "actions": None, "screenshot": "proof.png",
            }])
            self.assertEqual((local.root / "proof.png").read_bytes(), _SNAPSHOT)
            trace_events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
            artifact = next(event["data"]["artifact"] for event in trace_events if event["type"] == "artifact.created")
            self.assertEqual((root / artifact["path"]).read_bytes(), _SNAPSHOT)

        model_uses = next(event["data"] for event in trace_events if event["type"] == "model.finished")
        self.assertEqual(model_uses["web_usage"], {"web_search_requests": 1})
        self.assertEqual(model_uses["web_sources"], [{"title": "Pricing", "url": _PAGE}])
        first_request = create.call_args_list[0].kwargs
        tools = first_request["extra_body"]["tools"]
        self.assertIn("playwright", [item["function"]["name"] for item in tools if item["type"] == "function"])
        self.assertEqual([item["type"] for item in tools[-2:]], ["openrouter:web_search", "openrouter:web_fetch"])
        self.assertTrue(first_request["extra_body"]["provider"]["require_parameters"])
        page_read = next(event["data"] for event in trace_events if event["type"] == "browser.page_read")
        self.assertEqual(page_read["url"], _PAGE)
        transcript = create.call_args_list[1].kwargs["messages"]
        observations = {item["tool_call_id"]: item["content"] for item in transcript if item["role"] == "tool"}
        self.assertIn("web.browser_origins", observations["deny"])
        self.assertIn("loopback previews", observations["deny-command"])
