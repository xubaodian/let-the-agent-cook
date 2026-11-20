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
    def test_list_models_works_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(main(["--list-models"]), 0)

        catalog = json.loads(stdout.getvalue())
        self.assertEqual(catalog["chat"]["default"], "openai/gpt-5.4-mini")
        self.assertIn(catalog["chat"]["default"], catalog["chat"]["available"])
        self.assertGreater(len(catalog["chat"]["available"]), 1)
        self.assertIsNone(catalog["image"]["default"])
        self.assertIn("bytedance-seed/seedream-4.5", catalog["image"]["available"])

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
