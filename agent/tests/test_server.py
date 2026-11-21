import asyncio
import json
import os
import unittest
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from agent_cook.cli import main
from agent_cook.core.types import RunResult
from agent_cook.protocol.artifacts import ArtifactRef
from agent_cook.server.store import Job, RunStore

AUTH = {"Authorization": "Bearer fixture-token"}


def event_data(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


class RunStoreTest(unittest.TestCase):
    def test_server_lease_protects_active_runs_from_a_second_owner(self):
        with TemporaryDirectory() as directory:
            store = RunStore(Path(directory))
            with store.serve():
                with self.assertRaisesRegex(RuntimeError, "active server"), store.serve():
                    self.fail("Another service acquired the lease.")
            with store.serve():
                self.assertEqual(store.recover(), ())


@unittest.skipUnless(find_spec("fastapi") and find_spec("uvicorn"), "Install the server extra.")
class ServerConfigTest(unittest.TestCase):
    def test_service_uses_one_config_for_auth_models_and_its_executor(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "agent-cook.json").write_text(json.dumps({
                "api_keys": {"openrouter": "config-key", "exa": "config-exa-key"},
                "models": {"chat": {"default": "config/chat", "available": ["config/chat"]}},
                "server": {"token": "config-token", "host": "localhost", "port": 9456, "data_dir": "data"},
            }), encoding="utf-8")
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("agent_cook.settings.settings_directory", return_value=root),
                patch("uvicorn.run") as serve,
                patch("agent_cook.server.app.create_app") as app,
                patch("agent_cook.cli.run_task", new_callable=AsyncMock) as run,
            ):
                self.assertEqual(main(["serve", "-C", directory, "--port", "9457"]), 0)
                self.assertEqual(app.call_args.args[0], root / "data")
                options = app.call_args.kwargs
                self.assertEqual(options["token"], "config-token")
                self.assertEqual(options["chat_choices"], ("config/chat",))
                serve.assert_called_once_with(app.return_value, host="localhost", port=9457, workers=1)
                job = Job("run", "Check.", (), "config/chat", None)
                asyncio.run(options["execute"](job, {}, None, root))
                self.assertEqual(run.await_args.args[2], "config-key")
                self.assertEqual(run.await_args.kwargs["exa_api_key"], "config-exa-key")


@unittest.skipUnless(find_spec("fastapi") and find_spec("httpx"), "Install the server and MCP extras.")
class ServerTest(unittest.IsolatedAsyncioTestCase):
    async def test_upload_submission_replay_and_download(self):
        from httpx import ASGITransport, AsyncClient

        from agent_cook.server.app import create_app

        executions = []

        async def execute(job, inputs, events, artifact_dir):
            executions.append(job)
            events.emit("run.started", model=job.model)
            artifact_dir.mkdir(parents=True)
            file = artifact_dir / "result.txt"
            file.write_bytes(inputs["sample.txt"])
            artifact = ArtifactRef("result", f"{artifact_dir.name}/result.txt", "text/plain")
            events.emit("run.completed", text="Delivered.")
            return RunResult(job.id, "Delivered.", (artifact,))

        with TemporaryDirectory() as directory:
            app = create_app(
                Path(directory), token="fixture-token", execute=execute, workspace=True,
                chat_default="fixture/default", chat_choices=("fixture/choice",), image_choices=("fixture/image",),
            )
            async with app.router.lifespan_context(app), AsyncClient(
                transport=ASGITransport(app=app), base_url="http://fixture",
            ) as client:
                unauthorized = await client.get("/v1/models")
                self.assertEqual(unauthorized.status_code, 401)
                traversal = await client.post("/v1/files", params={"name": "../bad"}, content=b"x", headers=AUTH)
                self.assertEqual(traversal.status_code, 422)
                upload = await client.post("/v1/files", params={"name": "sample.txt"}, content=b"sample", headers=AUTH)
                self.assertEqual(upload.status_code, 201)
                task = {"task": "Deliver this file.", "file_ids": [upload.json()["file_id"]], "model": "fixture/choice"}
                headers = {**AUTH, "Idempotency-Key": "fixture-request"}
                submitted = await client.post("/v1/runs", json=task, headers=headers)
                self.assertEqual(submitted.status_code, 202)
                run_id = submitted.json()["run_id"]
                duplicate = await client.post("/v1/runs", json=task, headers=headers)
                self.assertEqual(duplicate.json()["run_id"], run_id)
                conflict = await client.post("/v1/runs", json={**task, "task": "Different."}, headers=headers)
                self.assertEqual(conflict.status_code, 409)
                injected = await client.post("/v1/runs", json={**task, "plugins": ["/host"]}, headers=AUTH)
                self.assertEqual(injected.status_code, 422)

                url = f"/v1/runs/{run_id}"
                streamed = await asyncio.wait_for(client.get(f"{url}/events", headers=AUTH), timeout=4)
                events = event_data(streamed)
                self.assertEqual([event["type"] for event in events], ["run.started", "run.completed"])
                self.assertEqual(events[0]["data"]["model"], "fixture/choice")
                replay = await client.get(f"{url}/events", headers={**AUTH, "Last-Event-ID": "1"})
                self.assertEqual(event_data(replay), events[1:])
                state = await client.get(url, headers=AUTH)
                self.assertEqual(state.json()["status"], "succeeded")
                artifact = await client.get(f"{url}/artifacts/result", headers=AUTH)
                self.assertEqual(artifact.content, b"sample")
                self.assertIn("attachment", artifact.headers["content-disposition"])
        self.assertEqual(len(executions), 1)

    async def test_restart_keeps_queued_jobs_and_cancellation_releases_the_runner(self):
        from httpx import ASGITransport, AsyncClient

        from agent_cook.server.app import create_app

        started = asyncio.Event()
        closed = asyncio.Event()

        async def execute(job, inputs, events, artifact_dir):
            events.emit("run.started", model=job.model)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = RunStore(root)
            store.enqueue(Job("old-run", "Interrupted task", (), "fixture/model", None), None)
            self.assertEqual(store.claim().id, "old-run")
            store.enqueue(Job("queued-run", "Run after restart", (), "fixture/model", None), None)
            reclaim = AsyncMock()
            app = create_app(
                root, token="fixture-token", execute=execute, chat_default="fixture/model", reclaim=reclaim,
            )
            async with app.router.lifespan_context(app), AsyncClient(
                transport=ASGITransport(app=app), base_url="http://fixture", headers=AUTH,
            ) as client:
                await asyncio.wait_for(started.wait(), timeout=4)
                recovered = await client.get("/v1/runs/old-run")
                self.assertEqual(recovered.json()["status"], "interrupted")
                reclaim.assert_awaited_once_with("old-run")
                cancelled = await client.post("/v1/runs/queued-run/cancel")
                self.assertEqual(cancelled.status_code, 202)
                await asyncio.wait_for(closed.wait(), timeout=4)
                streamed = await asyncio.wait_for(client.get("/v1/runs/queued-run/events"), timeout=4)
                self.assertEqual(event_data(streamed)[-1]["type"], "run.cancelled")
                state = await client.get("/v1/runs/queued-run")
                self.assertEqual(state.json()["status"], "cancelled")
