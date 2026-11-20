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
                    self.assertEqual(main(["--models-config", str(catalog), "--sandbox", "docker", *args]), 0)
                    self.assertEqual(run.await_args.args[0].model, chat_model)
                    self.assertEqual(run.await_args.kwargs["image_model"], image_model)
