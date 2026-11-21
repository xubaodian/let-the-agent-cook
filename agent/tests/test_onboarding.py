import base64
import hashlib
import io
import json
import os
import stat
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from subprocess import check_output
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
from unittest.mock import AsyncMock, patch

from agent_cook.cli import main
from agent_cook.core.types import RunResult
from agent_cook.onboarding import available_models, connect_browser
from agent_cook.settings import ACCOUNT, SERVICE


class Terminal(io.StringIO):
    def isatty(self):
        return True


class OnboardingTest(unittest.TestCase):
    def test_first_start_saves_key_and_model_and_can_skip_exa(self):
        with (
            TemporaryDirectory() as directory,
            patch.dict(os.environ, {}, clear=True),
            patch("agent_cook.settings.settings_directory", return_value=Path(directory)),
            patch("agent_cook.settings.stored_key", return_value=None),
            patch("sys.stdin", Terminal()),
            patch("agent_cook.cli.prompt_reader") as prompt,
            patch("agent_cook.onboarding.getpass.getpass", side_effect=["key-fixture", ""]),
            patch("agent_cook.onboarding.available_models", return_value=("demo/model",)),
            patch("agent_cook.onboarding._openrouter") as provider,
            patch("agent_cook.cli._run", new_callable=AsyncMock, return_value=RunResult("run", "Done.")) as run,
            redirect_stdout(Terminal()) as output,
            redirect_stderr(io.StringIO()) as diagnostics,
        ):
            prompt.return_value.side_effect = ["key", "1", "Hello.", "/config", "/exit"]
            self.assertEqual(main(["-C", directory]), 0)
            config = Path(directory) / "agent-cook.json"
            document = json.loads(config.read_bytes())
            self.assertEqual(document["api_keys"], {"openrouter": "key-fixture", "exa": None})
            self.assertEqual(document["models"]["chat"], {"default": "demo/model", "available": ["demo/model"]})
            self.assertIn(str(config.resolve()), output.getvalue())
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
            elif os.name == "nt":
                icacls = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/icacls.exe"
                permissions = check_output([icacls, config], encoding="utf-8", errors="replace")
                self.assertIn("(F)", permissions)
                self.assertNotIn("(I)", permissions)
            self.assertFalse((config.parent / "settings.json").exists())
        self.assertEqual(run.await_args.args[2], "key-fixture")
        self.assertIsNone(run.await_args.kwargs["exa_api_key"])
        provider.assert_called_once_with("/key", key="key-fixture")
        self.assertNotIn("key-fixture", output.getvalue() + diagnostics.getvalue())

    def test_editing_one_config_key_and_model_preserves_unrelated_settings(self):
        with (
            TemporaryDirectory() as directory,
            patch.dict(os.environ, {}, clear=True),
            patch("sys.stdin", Terminal()),
            patch("agent_cook.cli.prompt_reader") as prompt,
            patch("agent_cook.onboarding.getpass.getpass", side_effect=["new-exa-fixture", "invalid-key-fixture"]),
            patch("agent_cook.onboarding._openrouter", side_effect=ValueError("OpenRouter returned HTTP 401.")),
            patch("agent_cook.cli._run", new_callable=AsyncMock, return_value=RunResult("run", "Done.")) as run,
            redirect_stdout(Terminal()) as output,
            redirect_stderr(io.StringIO()) as diagnostics,
        ):
            config = Path(directory) / "my settings.json"
            config.write_text(json.dumps({
                "api_keys": {"openrouter": "original-key-fixture", "exa": "old-exa-fixture", "e2b": "e2b-fixture"},
                "models": {
                    "chat": {"default": "demo/model", "available": ["demo/model"]},
                    "image": {"default": "demo/image", "available": ["demo/image"]},
                    "chat_apis": {"demo/model": "responses"},
                },
                "server": {"token": "server-fixture"},
            }), encoding="utf-8")
            prompt.return_value.side_effect = [
                "/key", "exa", "/key openrouter", "/model demo/new", "/model", "/config", "Hello.", "/exit",
            ]
            arguments = ["-C", directory, "--config", config.name]
            self.assertEqual(main(arguments), 0)
            document = json.loads(config.read_bytes())
            self.assertEqual(document["api_keys"], {
                "openrouter": "original-key-fixture", "exa": "new-exa-fixture", "e2b": "e2b-fixture",
            })
            self.assertEqual(document["models"]["chat"], {
                "default": "demo/new", "available": ["demo/model", "demo/new"],
            })
            self.assertEqual(document["models"]["image"]["default"], "demo/image")
            self.assertEqual(document["models"]["chat_apis"], {"demo/model": "responses"})
            self.assertEqual(document["server"], {"token": "server-fixture"})
            self.assertEqual(main(["exec", *arguments, "Next launch."]), 0)
        for attempt in run.await_args_list:
            self.assertEqual(attempt.args[0].model, "demo/new")
            self.assertEqual(attempt.args[2], "original-key-fixture")
            self.assertEqual(attempt.kwargs["exa_api_key"], "new-exa-fixture")
        transcript = output.getvalue() + diagnostics.getvalue()
        self.assertIn("Current model  demo/new", transcript)
        self.assertIn("OpenRouter returned HTTP 401.", transcript)
        self.assertNotIn("-fixture", transcript)

    def test_key_command_reports_an_environment_override(self):
        with (
            TemporaryDirectory() as directory,
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "env-fixture", "OPENROUTER_MODEL": "demo/model"}, clear=True),
            patch("agent_cook.settings.settings_directory", return_value=Path(directory)),
            patch("sys.stdin", Terminal()),
            patch("agent_cook.cli.prompt_reader") as prompt,
            patch("agent_cook.onboarding.getpass.getpass", return_value="new-fixture"),
            patch("agent_cook.onboarding._openrouter"),
            redirect_stdout(Terminal()) as output,
            redirect_stderr(io.StringIO()),
        ):
            prompt.return_value.side_effect = ["/key openrouter", "/exit"]
            self.assertEqual(main(["-C", directory]), 0)
            config = json.loads((Path(directory) / "agent-cook.json").read_bytes())
            self.assertEqual(config["api_keys"]["openrouter"], "new-fixture")
        self.assertIn("configured via OPENROUTER_API_KEY", output.getvalue())
        self.assertNotIn("-fixture", output.getvalue())

    def test_existing_legacy_key_and_model_start_without_the_wizard(self):
        with (
            TemporaryDirectory() as directory,
            patch.dict(os.environ, {}, clear=True),
            patch("agent_cook.settings.settings_directory", return_value=Path(directory)),
            patch("agent_cook.settings._keyring") as keyring,
            patch("agent_cook.cli.connect") as wizard,
            patch("sys.stdin", Terminal()),
            patch("agent_cook.cli.prompt_reader") as prompt,
            redirect_stdout(Terminal()) as output,
            redirect_stderr(io.StringIO()),
        ):
            keyring.return_value.get_password.return_value = "legacy-fixture"
            (Path(directory) / "settings.json").write_text('{"model": "legacy/chat"}', encoding="utf-8")
            prompt.return_value.side_effect = ["/config", "/exit"]
            self.assertEqual(main(["-C", directory]), 0)
        wizard.assert_not_called()
        keyring.return_value.get_password.assert_called_with(SERVICE, ACCOUNT)
        self.assertIn("configured via legacy system credential store", output.getvalue())
        self.assertIn("Current model  legacy/chat", output.getvalue())

    def test_browser_authorization_uses_loopback_and_pkce(self):
        thread = None
        challenge = None

        def open_callback(url):
            nonlocal thread, challenge
            query = parse_qs(urlsplit(url).query)
            challenge = query["code_challenge"][0]
            callback = query["callback_url"][0]
            self.assertEqual(urlsplit(callback).hostname, "localhost")

            def receive():
                with urlopen(callback + "?code=fixture", timeout=5) as response:
                    self.assertEqual(response.status, 200)

            thread = Thread(target=receive, daemon=True)
            thread.start()
            return True

        with (
            patch("agent_cook.onboarding.webbrowser.open", side_effect=open_callback),
            patch("agent_cook.onboarding._openrouter", return_value={"key": "oauth-fixture"}) as exchange,
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(connect_browser(), "oauth-fixture")
            thread.join(timeout=5)
        data = exchange.call_args.kwargs["data"]
        self.assertEqual(data["code"], "fixture")
        self.assertEqual(data["code_challenge_method"], "S256")
        verifier_hash = hashlib.sha256(data["code_verifier"].encode()).digest()
        self.assertEqual(base64.urlsafe_b64encode(verifier_hash).decode().rstrip("="), challenge)

    def test_models_list_only_includes_tool_capable_text_output(self):
        with patch("agent_cook.onboarding._openrouter", return_value={"data": [
            {"id": "text/tools", "supported_parameters": ["tools"], "architecture": {"output_modalities": ["text"]}},
            {"id": "text/basic", "supported_parameters": [], "architecture": {"output_modalities": ["text"]}},
            {"id": "image/model", "supported_parameters": ["tools"], "architecture": {"output_modalities": ["image"]}},
        ]}):
            self.assertEqual(available_models(), ("text/tools",))
