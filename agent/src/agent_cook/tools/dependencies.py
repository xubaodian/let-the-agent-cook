import asyncio
import json
from typing import Any

from agent_cook.sandbox.dependencies import PACKAGE_BATCH_LIMIT, PACKAGE_SPEC_LENGTH, package_specs
from agent_cook.sandbox.interface import SandboxError
from agent_cook.sandbox.limits import MAX_COMMAND_SECONDS
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.spec import ToolContext, ToolDefinition, ToolError, ToolResult

INSTALL_DEPENDENCIES = "install_dependencies"
_DEFINITION = ToolDefinition(
    INSTALL_DEPENDENCIES,
    "Install additional Python or Node.js libraries for the current task. Use built-in workspace packages first. "
    "Provide public registry names and optional exact versions. Python uses wheels from PyPI. "
    "Node uses npm with lifecycle scripts disabled. The next run_command can import the installed packages.",
    {
        "type": "object",
        "properties": {
            "ecosystem": {"enum": ["python", "node"], "description": "Choose Python or Node.js."},
            "packages": {
                "type": "array", "minItems": 1, "maxItems": PACKAGE_BATCH_LIMIT, "uniqueItems": True,
                "items": {"type": "string", "minLength": 1, "maxLength": PACKAGE_SPEC_LENGTH},
                "description": "Examples: [\"humanize==4.13.0\"] for Python or [\"kleur@4.1.5\"] for Node.",
            },
        },
        "required": ["ecosystem", "packages"],
        "additionalProperties": False,
    },
)


def register_dependency_tool(registry: ToolRegistry) -> str:
    installed: dict[str, dict[str, str]] = {}
    lock = asyncio.Lock()

    async def install(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        ecosystem = arguments["ecosystem"]
        try:
            requested = package_specs(ecosystem, arguments["packages"])
        except ValueError as error:
            raise ToolError(str(error)) from error
        async with lock:
            combined = {**installed.get(ecosystem, {}), **requested}
            specifications = list(combined.values()) if ecosystem == "python" else [
                name if pin == "*" else f"{name}@{pin}" for name, pin in combined.items()
            ]
            sandbox = await context.get_sandbox()
            try:
                await sandbox.request(
                    INSTALL_DEPENDENCIES, {"ecosystem": ecosystem, "packages": specifications},
                    timeout_seconds=MAX_COMMAND_SECONDS + 10,
                )
            except SandboxError as error:
                if str(error) == "Unknown workspace operation or invalid arguments.":
                    raise ToolError("Update the Docker workspace image or E2B template to install packages.") from error
                raise
            installed[ecosystem] = combined
            return ToolResult(json.dumps({"ecosystem": ecosystem, "packages": specifications}))

    registry.register(_DEFINITION, install)
    return INSTALL_DEPENDENCIES
