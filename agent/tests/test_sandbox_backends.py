import asyncio
import json
import shlex
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from agent_cook.sandbox.client import WorkerSandbox
from agent_cook.sandbox.docker import DockerSandboxFactory
from agent_cook.sandbox.docker_setup import check_linux_docker
from agent_cook.sandbox.e2b import E2BSandboxFactory
from agent_cook.sandbox.interface import SandboxError, SandboxTransportError


def reply(**result):
    return json.dumps({"version": 1, "ok": True, "result": result}).encode("utf-8")


class Stream:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def __aiter__(self):
        yield self.payload[:10]
        yield self.payload[10:]


class RateLimitException(Exception):
    pass


class RequestTimeout(Exception):
    pass


def e2b_sdk(create):
    return SimpleNamespace(
        AsyncSandbox=SimpleNamespace(create=create),
        exceptions=SimpleNamespace(RateLimitException=RateLimitException, TimeoutException=RequestTimeout),
    )


class BackendTest(unittest.IsolatedAsyncioTestCase):
    async def test_docker_uses_a_private_workspace_and_removes_it_on_error(self):
        command = AsyncMock(side_effect=[
            (0, b"container\n", b""),
            (0, reply(entries=[]), b""),
            (0, b"", b""),
        ])
        with (
            patch("agent_cook.sandbox.docker._docker", command),
            self.assertRaisesRegex(RuntimeError, "fixture"),
        ):
            async with DockerSandboxFactory().open("run-1") as sandbox:
                self.assertEqual(await sandbox.request("list_files", {}), {"entries": []})
                raise RuntimeError("fixture")

        start, worker, cleanup = command.call_args_list
        self.assertEqual(start.args[0], "run")
        self.assertEqual(start.args[start.args.index("--network") + 1], "none")
        self.assertNotIn("--volume", start.args)
        self.assertIn("--user=10001:10001", start.args)
        self.assertEqual(worker.args[:2], ("exec", "-i"))
        self.assertIn("/usr/local/bin/python", worker.args)
        self.assertEqual(json.loads(worker.kwargs["payload"])["operation"], "list_files")
        self.assertEqual(cleanup.args[-1], start.args[start.args.index("--name") + 1])
        self.assertEqual(cleanup.args[:2], ("rm", "--force"))

    async def test_e2b_uses_the_same_request_and_closes_the_lease(self):
        files = SimpleNamespace(
            write=AsyncMock(),
            read=AsyncMock(return_value=Stream(reply(path="notes.txt", bytes=1))),
            remove=AsyncMock(),
        )
        session = SimpleNamespace(
            files=files, commands=SimpleNamespace(run=AsyncMock()), kill=AsyncMock()
        )
        create = AsyncMock(return_value=session)
        with patch.dict(sys.modules, {"e2b": e2b_sdk(create)}):
            async with E2BSandboxFactory("fixture-template", api_key="fixture-e2b-key").open("run-2") as sandbox:
                result = await sandbox.request("write_file", {"path": "notes.txt", "content": "x"})

        self.assertEqual(result, {"path": "notes.txt", "bytes": 1})
        self.assertEqual(create.call_args.kwargs["api_key"], "fixture-e2b-key")
        self.assertFalse(create.call_args.kwargs["allow_internet_access"])
        self.assertFalse(create.call_args.kwargs["network"]["allow_public_traffic"])
        self.assertEqual(create.call_args.kwargs["metadata"], {"agent_cook_run": "run-2"})
        request, payload = files.write.call_args.args
        args = shlex.split(session.commands.run.call_args.args[0])
        self.assertEqual(args[0], "/usr/local/bin/python")
        self.assertEqual(args[args.index("--request") + 1], request)
        self.assertEqual(args[args.index("--response") + 1], files.read.call_args.args[0])
        self.assertEqual(session.commands.run.call_args.kwargs["cwd"], "/workspace")
        self.assertEqual(json.loads(payload)["operation"], "write_file")
        self.assertEqual(files.remove.await_count, 2)
        session.kill.assert_awaited_once()

    async def test_docker_retries_start_read_and_cleanup_with_the_same_container_name(self):
        interrupted = (125, b"", b"connection reset by peer")
        ok = (0, b"", b"")
        command = AsyncMock(side_effect=[
            interrupted, ok, ok, interrupted, (0, reply(entries=["file.txt"]), b""), interrupted, ok,
        ])
        with (
            patch("agent_cook.sandbox.docker._docker", command),
            patch("agent_cook.retry.asyncio.sleep", new_callable=AsyncMock),
        ):
            async with DockerSandboxFactory().open("run-retry") as sandbox:
                self.assertEqual(await sandbox.request("list_files", {}), {"entries": ["file.txt"]})

        calls = [call.args for call in command.call_args_list]
        self.assertEqual([args[0] for args in calls], ["run", "rm", "run", "exec", "exec", "rm", "rm"])
        self.assertEqual(calls[0], calls[2])
        self.assertEqual(calls[3], calls[4])
        name = calls[0][calls[0].index("--name") + 1]
        self.assertTrue(all(args[-1] == name for args in (calls[1], calls[5], calls[6])))

    async def test_docker_rejects_a_missing_image_without_retrying_start(self):
        command = AsyncMock(side_effect=[
            (125, b"", b"No such image: agent-cook-sandbox:local"),
            (1, b"", b"No such container"),
        ])
        with (
            patch("agent_cook.sandbox.docker._docker", command),
            self.assertRaisesRegex(SandboxError, "No such image"),
        ):
            async with DockerSandboxFactory().open("missing-image"):
                self.fail("An invalid image cannot open a sandbox.")
        self.assertEqual([call.args[0] for call in command.call_args_list], ["run", "rm"])

    async def test_read_retries_are_bounded_and_commands_and_writes_are_sent_once(self):
        for operation, expected in (("read_file", 3), ("write_file", 1), ("upload", 1), ("run_command", 1)):
            with self.subTest(operation=operation):
                exchange = AsyncMock(side_effect=SandboxTransportError("connection lost"))
                sandbox = WorkerSandbox(exchange)
                with (
                    patch("agent_cook.retry.asyncio.sleep", new_callable=AsyncMock),
                    self.assertRaises(SandboxTransportError),
                ):
                    await sandbox.request(operation, {})
                self.assertEqual(exchange.await_count, expected)

    async def test_worker_errors_and_cancellation_stop_retries(self):
        exchange = AsyncMock(return_value=b'{"version": 1, "ok": false, "error": "File not found"}')
        sandbox = WorkerSandbox(exchange)
        with self.assertRaisesRegex(SandboxError, "File not found"):
            await sandbox.request("read_file", {"path": "missing.txt"})
        exchange.assert_awaited_once()

        exchange.reset_mock(side_effect=True)
        exchange.side_effect = SandboxTransportError("connection lost")
        sleeping = asyncio.Event()

        async def backoff(_):
            sleeping.set()
            await asyncio.Future()

        with patch("agent_cook.retry.asyncio.sleep", side_effect=backoff):
            task = asyncio.create_task(sandbox.request("read_file", {"path": "notes.txt"}))
            await asyncio.wait_for(sleeping.wait(), timeout=1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        exchange.assert_awaited_once()

    async def test_e2b_retries_declined_creation_and_recovers_file_transfers_without_replaying_the_write(self):
        files = SimpleNamespace(
            write=AsyncMock(side_effect=[RequestTimeout(), None]),
            read=AsyncMock(side_effect=[RequestTimeout(), Stream(reply(path="notes.txt", bytes=1))]),
            remove=AsyncMock(),
        )
        session = SimpleNamespace(
            files=files, commands=SimpleNamespace(run=AsyncMock()),
            kill=AsyncMock(side_effect=[RequestTimeout(), True]),
        )
        create = AsyncMock(side_effect=[RateLimitException(), session])
        with (
            patch.dict(sys.modules, {"e2b": e2b_sdk(create)}),
            patch("agent_cook.retry.asyncio.sleep", new_callable=AsyncMock),
        ):
            async with E2BSandboxFactory("fixture-template").open("e2b-retry") as sandbox:
                self.assertEqual(
                    await sandbox.request("write_file", {"path": "notes.txt", "content": "x"}),
                    {"path": "notes.txt", "bytes": 1},
                )
        self.assertEqual(create.await_count, 2)
        self.assertEqual(files.write.await_count, 2)
        self.assertEqual(files.write.await_args_list[0], files.write.await_args_list[1])
        session.commands.run.assert_awaited_once()
        self.assertEqual(files.read.await_count, 2)
        self.assertEqual(files.read.await_args_list[0], files.read.await_args_list[1])
        self.assertEqual(session.kill.await_count, 2)

    async def test_e2b_creation_timeout_does_not_open_another_lease(self):
        create = AsyncMock(side_effect=RequestTimeout())
        with (
            patch.dict(sys.modules, {"e2b": e2b_sdk(create)}),
            self.assertRaisesRegex(SandboxError, "could not open the task sandbox"),
        ):
            async with E2BSandboxFactory("fixture-template").open("ambiguous-create"):
                self.fail("No sandbox was confirmed.")
        create.assert_awaited_once()


class DockerSetupTest(unittest.TestCase):
    def test_local_docker_probe_recovers_when_the_daemon_is_starting(self):
        run = Mock(side_effect=[
            SimpleNamespace(returncode=1, stdout="", stderr="Cannot connect to the Docker daemon"),
            SimpleNamespace(returncode=0, stdout="linux\n", stderr=""),
        ])
        with (
            patch.dict("os.environ", {"DOCKER_HOST": "unix:///var/run/docker.sock", "DOCKER_CONTEXT": ""}),
            patch("agent_cook.sandbox.docker_setup.run", run),
            patch("agent_cook.sandbox.docker_setup.sleep") as sleep,
        ):
            check_linux_docker()
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(0.25)
