import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from agent_cook.cli import main
from agent_cook.core.types import RunResult
from agent_cook.model.catalog import ModelCatalog


class ModelCatalogTest(unittest.TestCase):
    def test_only_an_explicit_path_can_replace_the_user_configuration(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "user"
            instance = root / "folder"
            for path, model in ((config, "user/chat"), (instance, "folder/chat")):
                path.mkdir()
                (path / "agent-cook.json").write_text(json.dumps({
                    "models": {"chat": {"available": [model], "default": model}},
                }), encoding="utf-8")
            with patch("agent_cook.settings.settings_directory", return_value=config):
                for args, expected in (([], "user/chat"), (["--config", "agent-cook.json"], "folder/chat")):
                    with self.subTest(args=args), redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(main(["-C", str(instance), "--list-models", *args]), 0)
                    self.assertEqual(json.loads(output.getvalue())["chat"]["default"], expected)

    def test_unified_config_applies_cli_defaults_without_overriding_environment_or_flags(self):
        with TemporaryDirectory() as directory:
            config = Path(directory) / "agent-cook.json"
            config.write_text(json.dumps({
                "api_keys": {"openrouter": "config-key", "exa": "config-exa-key"},
                "models": {
                    "chat": {"default": "config/chat", "available": ["config/chat"]},
                    "image": {"default": "config/image", "available": ["config/image"]},
                },
                "model_api": "responses",
                "sandbox": {"backend": "docker", "docker_image": "config/sandbox", "network": True},
                "run": {"max_turns": 12, "timeout": 180},
            }), encoding="utf-8")
            cases = (
                ({}, [], "config-key", "config-exa-key", "config/chat", "config/image", 12),
                ({"OPENROUTER_API_KEY": "env-key", "EXA_API_KEY": "env-exa-key", "OPENROUTER_MODEL": "env/chat"},
                 ["--image-model", "cli/image", "--max-turns", "9"],
                 "env-key", "env-exa-key", "env/chat", "cli/image", 9),
            )
            for env, args, key, exa, chat, image, max_turns in cases:
                with (
                    self.subTest(chat=chat), patch.dict(os.environ, env, clear=True),
                    patch("agent_cook.settings.settings_directory", return_value=config.parent),
                    patch("agent_cook.cli.NativeSandboxFactory") as factory,
                    patch("agent_cook.cli._run", new_callable=AsyncMock, return_value=RunResult("run", "Done.")) as run,
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
                ):
                    self.assertEqual(main(["exec", "-C", directory, *args, "Check."]), 0)
                spec = run.await_args.args[0]
                self.assertEqual((spec.model, spec.max_model_turns), (chat, max_turns))
                self.assertEqual(run.await_args.args[2], key)
                self.assertEqual(run.await_args.kwargs["exa_api_key"], exa)
                self.assertEqual(run.await_args.kwargs["image_model"], image)
                self.assertEqual(run.await_args.kwargs["model_api"], "responses")
                factory.assert_called_once_with()

    def test_legacy_timeout_config_leaves_the_default_turn_budget_unset(self):
        with TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(json.dumps({"run": {"timeout": 120}}), encoding="utf-8")
            with (
                patch.dict(os.environ, {"OPENROUTER_API_KEY": "fixture-key", "OPENROUTER_MODEL": "fixture/model"}, clear=True),
                patch("agent_cook.cli._run", new_callable=AsyncMock, return_value=RunResult("run", "Done.")) as run,
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(main(["exec", "-C", directory, "--config", str(config), "Check."]), 0)
            self.assertIsNone(run.await_args.args[0].max_model_turns)

    def test_list_models_has_no_defaults_without_user_configuration(self):
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(main(["--list-models"]), 0)

        catalog = json.loads(stdout.getvalue())
        for group in ("chat", "image"):
            self.assertEqual(catalog[group], {"default": None, "available": []})

    def test_list_user_models_without_credentials(self):
        catalog = {
            "chat": {"default": "fixture/chat", "available": ["fixture/chat", "fixture/other-chat"]},
            "image": {"default": None, "available": ["fixture/image"]},
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            path.write_text(json.dumps(catalog), encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main(["--models-config", str(path), "--list-models"]), 0)
        self.assertEqual(json.loads(stdout.getvalue()), catalog)

    def test_task_requires_a_chat_model_from_the_user(self):
        with (
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "fixture-key"}, clear=True),
            patch("agent_cook.cli._run", new_callable=AsyncMock) as run,
            redirect_stderr(io.StringIO()) as stderr,
            self.assertRaises(SystemExit) as error,
        ):
            main(["Check."])

        self.assertEqual(error.exception.code, 2)
        self.assertIn("Choose a chat model", stderr.getvalue())
        run.assert_not_awaited()

    def test_catalog_default_must_be_in_its_own_list(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            path.write_text(json.dumps({
                "chat": {"available": ["fixture/chat"]},
                "image": {"default": "fixture/chat", "available": ["fixture/image"]},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"image\.available"):
                ModelCatalog.load(path)

    def test_cli_resolves_chat_and_image_models_independently(self):
        environment = {"OPENROUTER_API_KEY": "fixture-key"}
        overrides = {"OPENROUTER_MODEL": "env/chat", "OPENROUTER_IMAGE_MODEL": "env/image"}
        run_result = RunResult("run", "Done.")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "models.json"
            catalog.write_text(json.dumps({
                "chat": {"default": "catalog/chat", "available": ["catalog/chat"]},
                "image": {"default": "catalog/image", "available": ["catalog/image"]},
            }), encoding="utf-8")
            request = root / "request.json"
            request.write_text(json.dumps({
                "task": "Check.", "model": "request/chat", "image_model": "request/image",
            }), encoding="utf-8")
            request_args = ["--request", str(request)]
            cases = (
                (["Check."], {}, "catalog/chat", "catalog/image"),
                (["Check."], overrides, "env/chat", "env/image"),
                (request_args, overrides, "request/chat", "request/image"),
                (["--model", "cli/chat", *request_args], overrides, "cli/chat", "request/image"),
                (["--image-model", "cli/image", *request_args], overrides, "request/chat", "cli/image"),
            )
            for args, env, chat_model, image_model in cases:
                with (
                    self.subTest(chat=chat_model, image=image_model),
                    patch.dict(os.environ, {**environment, **env}, clear=True),
                    patch("agent_cook.cli._run", new_callable=AsyncMock, return_value=run_result) as run,
                    redirect_stdout(io.StringIO()),
                    redirect_stderr(io.StringIO()),
                ):
                    self.assertEqual(main(["--models-config", str(catalog), "--sandbox", "native", *args]), 0)
                    self.assertEqual(run.await_args.args[0].model, chat_model)
                    self.assertEqual(run.await_args.kwargs["image_model"], image_model)
