import json
import re
from dataclasses import dataclass
from io import BytesIO
from math import isfinite
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from agent_cook.core.types import SubagentSpec
from agent_cook.extensions.files import FileTree
from agent_cook.extensions.frontmatter import read_frontmatter
from agent_cook.extensions.hooks import HookSpec
from agent_cook.extensions.mcp import McpServer, read_servers
from agent_cook.sandbox.limits import MAX_COMMAND_SECONDS, MAX_TEXT_FILE_BYTES
from agent_cook.sandbox.paths import workspace_path
from agent_cook.sandbox.worker.workspace import Workspace

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
_STRING = {"type": "string", "minLength": 1}
_TOOL = {**_STRING, "pattern": "^[A-Za-z0-9_-]{1,64}$"}
_HOOKS_PATH = "hooks/hooks.json"
_AGENT = Draft202012Validator({
    "type": "object",
    "required": ["name", "description"],
    "properties": {
        "name": _TOOL, "description": _STRING, "model": _STRING,
        "tools": {"type": "array", "items": _TOOL, "uniqueItems": True},
        "max_model_turns": {"type": "integer", "minimum": 1},
        "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
    },
    "additionalProperties": False,
})
_HOOK = Draft202012Validator({
    "type": "object",
    "required": ["event", "tool", "script"],
    "properties": {
        "event": {"enum": ["before_tool", "after_tool"]},
        "tool": _TOOL,
        "script": _STRING,
        "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": MAX_COMMAND_SECONDS},
    },
    "additionalProperties": False,
})


@dataclass(frozen=True, slots=True)
class Plugin:
    name: str
    skills_dir: Path | None
    subagents: tuple[SubagentSpec, ...]
    hooks: tuple[HookSpec, ...]
    files: tuple[FileTree, ...]
    mcp_servers: tuple[McpServer, ...]


def load_plugins(directories: tuple[Path, ...]) -> tuple[Plugin, ...]:
    plugins = []
    names = set()
    agents = set()
    servers = set()
    for directory in directories:
        try:
            plugin = _load(directory)
        except (OSError, ValueError) as error:
            raise ValueError(f"Could not load plugin '{directory}': {error}") from error
        if plugin.name in names:
            raise ValueError(f"Plugin '{plugin.name}' is enabled more than once.")
        names.add(plugin.name)
        for agent in plugin.subagents:
            if agent.name in agents:
                raise ValueError(f"Subagent '{agent.name}' is defined more than once.")
            agents.add(agent.name)
        for server in plugin.mcp_servers:
            if server.name in servers:
                raise ValueError(f"MCP server '{server.name}' is defined more than once.")
            servers.add(server.name)
        plugins.append(plugin)
    return tuple(plugins)


def _load(directory: Path) -> Plugin:
    source = Workspace(directory)
    manifest = _read_json(source, "plugin.json")
    name = manifest.get("name")
    if (
        manifest.get("$schema") != PLUGIN_SCHEMA
        or not isinstance(name, str) or len(name) > 64
        or re.fullmatch(r"(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", name) is None
    ):
        raise ValueError("plugin.json must declare the Agent Plugins schema and a valid name.")
    entries, limited = source.entries(".", recursive=False)
    if limited:
        raise ValueError("The plugin directory has too many entries.")
    kinds = dict(entries)
    dirs = {path for path, kind in entries if kind == "directory"}
    agents = _load_agents(source) if "agents" in dirs else ()
    hooks = _load_hooks(source, name) if "hooks" in dirs else ()
    files = tuple(
        FileTree(source, path, exclude=frozenset({_HOOKS_PATH}))
        for path in ("assets", "hooks") if path in dirs
    )
    servers = read_servers(source.root, _read_json(source, "mcp.json")) if kinds.get("mcp.json") == "file" else ()
    return Plugin(name, source.root / "skills" if "skills" in dirs else None, agents, hooks, files, servers)


def _read_json(source: Workspace, path: str) -> dict[str, Any]:
    try:
        data = json.loads(source.read_bytes(path, MAX_TEXT_FILE_BYTES))
    except ValueError as error:
        raise ValueError(f"Could not read {path} as JSON: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a JSON object.")
    return data


def _load_agents(source: Workspace) -> tuple[SubagentSpec, ...]:
    entries, limited = source.entries("agents", recursive=False)
    if limited:
        raise ValueError("The plugin agent directory has too many entries.")
    agents = []
    for path, kind in entries:
        if kind != "file" or not path.endswith(".md"):
            continue
        with BytesIO(source.read_bytes(path, MAX_TEXT_FILE_BYTES)) as stream:
            metadata = read_frontmatter(stream)
            instructions = stream.read().decode("utf-8").strip()
        try:
            _AGENT.validate(metadata)
        except ValidationError as error:
            raise ValueError(f"Invalid subagent in '{path}' at {error.json_path}.") from error
        model = metadata.get("model")
        agents.append(SubagentSpec(
            name=metadata["name"], description=metadata["description"], instructions=instructions,
            tool_names=tuple(metadata.get("tools", ())), model=None if model == "inherit" else model,
            max_model_turns=int(metadata.get("max_model_turns", 4)), timeout_seconds=metadata.get("timeout_seconds", 45),
        ))
    return tuple(agents)


def _load_hooks(source: Workspace, plugin: str) -> tuple[HookSpec, ...]:
    try:
        document = _read_json(source, _HOOKS_PATH)
    except FileNotFoundError:
        return ()
    hooks = document.get("hooks")
    if not isinstance(hooks, list):
        raise ValueError("hooks/hooks.json requires a hooks array.")
    result = []
    for index, item in enumerate(hooks):
        try:
            _HOOK.validate(item)
        except ValidationError as error:
            raise ValueError(f"Invalid hook at index {index} at {error.json_path}.") from error
        timeout = item.get("timeout_seconds", 10)
        if not isfinite(timeout):
            raise ValueError(f"Hook at index {index} requires a finite timeout.")
        path = workspace_path(item["script"])
        code = source.read_bytes(path, MAX_TEXT_FILE_BYTES).decode("utf-8")
        result.append(HookSpec(
            plugin=plugin, event=item["event"], tool=item["tool"], script=path,
            source=code, timeout_seconds=timeout,
        ))
    return tuple(result)
