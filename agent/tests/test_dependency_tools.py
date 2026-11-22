import json
import os
import unittest
from unittest.mock import AsyncMock

from agent_cook.sandbox.docker import DockerSandboxFactory
from agent_cook.sandbox.interface import SandboxError
from agent_cook.sandbox.native.factory import NativeSandboxFactory
from agent_cook.tools.dependencies import INSTALL_DEPENDENCIES, register_dependency_tool
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.spec import ToolCall, ToolContext

IMAGE = os.environ.get("AGENT_COOK_TEST_DOCKER_IMAGE")


async def install(registry, sandbox, ecosystem, *packages):
    async def get_sandbox():
        return sandbox

    name = INSTALL_DEPENDENCIES
    context = ToolContext("dependency-test", frozenset({name}), get_sandbox)
    arguments = json.dumps({"ecosystem": ecosystem, "packages": list(packages)})
    return await registry.invoke(ToolCall("install", name, arguments), context)


class DependencyToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        register_dependency_tool(self.registry)

    async def test_public_package_specs_are_validated_before_dispatch(self):
        workspace = AsyncMock()
        for ecosystem, package in (
            ("python", "-r requirements.txt"), ("python", "pkg @ https://example.invalid/a.whl"),
            ("node", "git+https://example.invalid/repo"), ("node", "name@file:./package"),
        ):
            with self.subTest(ecosystem=ecosystem, package=package):
                result = await install(self.registry, workspace, ecosystem, package)
                self.assertTrue(result.is_error)
        workspace.request.assert_not_awaited()

    async def test_failed_request_does_not_change_the_packages_for_the_next_install(self):
        workspace = AsyncMock()
        workspace.request.side_effect = [{}, SandboxError("No wheel."), {}]
        self.assertFalse((await install(self.registry, workspace, "python", "humanize==4.13.0")).is_error)
        self.assertTrue((await install(self.registry, workspace, "python", "missing")).is_error)
        self.assertFalse((await install(self.registry, workspace, "python", "Babel==2.17.0")).is_error)
        self.assertEqual(workspace.request.call_args.args[1]["packages"], ["humanize==4.13.0", "Babel==2.17.0"])

    async def check_registry_install(self, factory):
        async with factory.open("dependency-test") as workspace:
            for ecosystem, spec, argv, expected in (
                ("python", "humanize==4.13.0", ["python", "-c", "from humanize import intcomma; print(intcomma(1234))"], "1,234"),
                ("node", "kleur@4.1.5", [
                    "node", "--input-type=module", "-e", "import kleur from 'kleur'; console.log(kleur.green('ready'))",
                ], "ready"),
            ):
                result = await install(self.registry, workspace, ecosystem, spec)
                self.assertFalse(result.is_error, result.model_content)
                command = await workspace.exec(argv, cwd=".", timeout_seconds=25)
                self.assertEqual(command.exit_code, 0, command.stderr)
                self.assertEqual(command.stdout.strip(), expected)

        async with factory.open("fresh-task") as workspace:
            clean = await workspace.exec(["python", "-c", "import humanize"], cwd=".", timeout_seconds=20)
            self.assertNotEqual(clean.exit_code, 0)
            self.assertIn("No module named 'humanize'", clean.stderr)

    @unittest.skipUnless(os.environ.get("AGENT_COOK_TEST_NATIVE") == "1", "Needs native isolation and registry access.")
    async def test_native_installs_python_and_node_into_the_task(self):
        await self.check_registry_install(NativeSandboxFactory())

    @unittest.skipUnless(IMAGE, "Needs a Docker workspace image and registry access.")
    async def test_docker_installs_python_and_node_into_the_task(self):
        await self.check_registry_install(DockerSandboxFactory(IMAGE, allow_network=True))
