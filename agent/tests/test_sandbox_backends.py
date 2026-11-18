import json
import shlex
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent_cook.sandbox.docker import DockerSandboxFactory
from agent_cook.sandbox.e2b import E2BSandboxFactory


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
        sdk = SimpleNamespace(AsyncSandbox=SimpleNamespace(create=create))
        with patch.dict(sys.modules, {"e2b": sdk}):
            async with E2BSandboxFactory("fixture-template").open("run-2") as sandbox:
                result = await sandbox.request("write_file", {"path": "notes.txt", "content": "x"})

        self.assertEqual(result, {"path": "notes.txt", "bytes": 1})
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
