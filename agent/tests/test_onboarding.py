import base64
import hashlib
import io
import json
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
from unittest.mock import Mock, patch

from agent_cook.model.catalog import ModelChoices
from agent_cook.onboarding import available_models, connect, connect_browser
from agent_cook.settings import ACCOUNT, SERVICE, saved_model, stored_key


class OnboardingTest(unittest.TestCase):
    def test_pasted_key_is_saved_to_credential_store_and_model_to_config(self):
        keyring = Mock()
        keyring.get_password.return_value = "key-fixture"
        with (
            TemporaryDirectory() as directory,
            patch("agent_cook.settings.settings_directory", return_value=Path(directory)),
            patch("agent_cook.settings._keyring", return_value=keyring),
            patch("agent_cook.onboarding.getpass.getpass", return_value="key-fixture"),
            patch("agent_cook.onboarding._openrouter") as provider,
            redirect_stderr(io.StringIO()) as diagnostics,
        ):
            answers = iter(["key", ""])
            self.assertEqual(connect(
                lambda _: next(answers), ModelChoices(("demo/model",), "demo/model"), api_key=None, model=None,
            ), ("key-fixture", "demo/model"))
            self.assertEqual(saved_model(), "demo/model")
            self.assertEqual(stored_key(), "key-fixture")
            self.assertEqual(json.loads((Path(directory) / "settings.json").read_bytes()), {"model": "demo/model"})
        keyring.set_password.assert_called_once_with(SERVICE, ACCOUNT, "key-fixture")
        keyring.get_password.assert_called_once_with(SERVICE, ACCOUNT)
        provider.assert_called_once_with("/key", key="key-fixture")
        self.assertNotIn("key-fixture", diagnostics.getvalue())

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
