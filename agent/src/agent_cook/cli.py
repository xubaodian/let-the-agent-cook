import argparse
import asyncio
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import replace
from functools import partial
from math import ceil
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from agent_cook.core.run import run_agent
from agent_cook.core.types import AgentRunError, AgentSpec, RunResult
from agent_cook.events.journal import RunEvents, write_jsonl
from agent_cook.extensions.hooks import HookRunner
from agent_cook.extensions.mcp import register_mcp_tools
from agent_cook.model.openrouter import OPENROUTER_BASE_URL, OpenRouterModel
from agent_cook.model.openrouter_images import OpenRouterImageGenerator
from agent_cook.protocol.request import RunRequest
from agent_cook.sandbox.docker import DEFAULT_DOCKER_IMAGE, DockerSandboxFactory
from agent_cook.sandbox.e2b import E2BSandboxFactory
from agent_cook.sandbox.interface import SandboxError, SandboxFactory
from agent_cook.sandbox.limits import MAX_FILE_BYTES, MAX_TRANSFER_BYTES
from agent_cook.sandbox.paths import workspace_path
from agent_cook.tools.artifacts import ArtifactExporter
from agent_cook.tools.images import register_image_tool
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.workspace import workspace_tools

if TYPE_CHECKING:
    from agent_cook.extensions.plugins import Plugin
    from agent_cook.extensions.skills import SkillCatalog


def _input_files(paths: list[Path]) -> dict[str, bytes]:
    inputs = {}
    remaining = MAX_TRANSFER_BYTES
    for path in paths:
        name = workspace_path(path.name)
        if name in inputs:
            raise ValueError(f"Multiple input files have the name '{name}'.")
        if not path.is_file():
            raise ValueError(f"Input '{path}' must be a regular file.")
        limit = min(MAX_FILE_BYTES, remaining)
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"Input '{path}' exceeds the file or batch transfer limit.")
        inputs[name] = data
        remaining -= len(data)
    return inputs


async def _run(
    spec: AgentSpec,
    task: str,
    api_key: str,
    trace_path: Path,
    run_id: str,
    factory: SandboxFactory | None,
    inputs: dict[str, bytes],
    *,
    image_model: str | None = None,
    skills: "SkillCatalog | None" = None,
    plugins: "tuple[Plugin, ...]" = (),
) -> RunResult:
    from openai import AsyncOpenAI

    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("x", encoding="utf-8") as trace:
        events = RunEvents(lambda event: write_jsonl(trace, event), run_id=run_id)
        async with AsyncExitStack() as resources:
            client = await resources.enter_async_context(AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL))
            registry = ToolRegistry()
            tool_names: tuple[str, ...] = ()
            if factory is not None:
                exporter = ArtifactExporter(trace_path.parent / f"{run_id}-artifacts")
                registry, tool_names = workspace_tools(exporter)
                if image_model is not None:
                    image_tool = register_image_tool(registry, OpenRouterImageGenerator(client, image_model))
                    tool_names = (*tool_names, image_tool)
            servers = tuple(server for plugin in plugins for server in plugin.mcp_servers)
            tool_names += await register_mcp_tools(resources, registry, servers)
            spec = replace(
                spec, tool_names=tool_names,
                instructions=skills.instructions(spec.instructions) if skills is not None else spec.instructions,
            )
            hooks = tuple(hook for plugin in plugins for hook in plugin.hooks)
            trees = tuple(tree for plugin in plugins for tree in plugin.files)
            workspace_inputs = (
                partial(skills.workspace_files, inputs, extra_trees=trees)
                if skills is not None and (skills.names or trees) else inputs
            )
            if plugins or skills is not None:
                events.emit(
                    "extensions.loaded", plugins=[plugin.name for plugin in plugins],
                    skills=list(skills.names) if skills is not None else [],
                )
            return await run_agent(
                spec,
                task,
                model=OpenRouterModel(client),
                tools=registry,
                sandbox_factory=factory,
                input_files=workspace_inputs,
                events=events,
                subagents=tuple(agent for plugin in plugins for agent in plugin.subagents),
                hook=HookRunner(hooks) if hooks else None,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one task with the OpenRouter model you configure.")
    parser.add_argument("task", nargs="?", help="Text to send to the agent")
    parser.add_argument("--request", type=Path, help="Read the task and enabled extensions from a JSON request")
    parser.add_argument(
        "--model", default=os.environ.get("OPENROUTER_MODEL"), help="OpenRouter model ID"
    )
    parser.add_argument("--image-model", help="OpenRouter Image API model ID (or set OPENROUTER_IMAGE_MODEL)")
    parser.add_argument("--trace", type=Path, help="Write the run to a new JSONL file at this path")
    parser.add_argument("--sandbox", choices=("docker", "e2b"), help="Enable task workspace tools")
    parser.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE, help="Prebuilt local sandbox image")
    parser.add_argument("--sandbox-network", action="store_true", help="Enable outbound sandbox networking")
    parser.add_argument("--input", action="append", type=Path, default=[], help="Copy a local input file into the workspace")
    parser.add_argument(
        "--skills-dir", action="append", type=Path, default=[], help="Load a directory of Skill packages",
    )
    parser.add_argument("--skill", action="append", help="Mount a named Skill. Repeat to select several.")
    parser.add_argument("--plugin", action="append", type=Path, default=[], help="Enable a local plugin directory")
    parser.add_argument("--max-turns", type=int, default=8, help="Maximum number of model turns")
    parser.add_argument("--timeout", type=float, default=120, help="Run time limit in seconds")
    args = parser.parse_args(argv)

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        parser.error("Set OPENROUTER_API_KEY before running the agent.")
    if not args.model or not args.model.strip():
        parser.error("Set OPENROUTER_MODEL or pass --model with an OpenRouter model ID.")
    if args.request is not None and (args.task is not None or args.plugin or args.skills_dir or args.skill):
        parser.error("Specify the task, plugins, and skills in the JSON file when using --request.")
    if args.request is None and (args.task is None or not args.task.strip()):
        parser.error("Provide a task with at least one non-whitespace character, or pass --request.")

    if (args.input or args.sandbox_network) and args.sandbox is None:
        parser.error("--input and --sandbox-network require --sandbox.")
    if args.skills_dir and args.sandbox is None:
        parser.error("--skills-dir requires --sandbox.")
    if args.image_model is not None and args.sandbox is None:
        parser.error("--image-model requires --sandbox.")
    image_model = args.image_model if args.image_model is not None else os.environ.get("OPENROUTER_IMAGE_MODEL", "")
    try:
        request = RunRequest.from_file(args.request) if args.request is not None else RunRequest(
            task=args.task, plugins=tuple(args.plugin), skill_directories=tuple(args.skills_dir),
            mounted_skills=tuple(args.skill) if args.skill is not None else None,
        )
        spec = AgentSpec(
            model=args.model.strip(), max_model_turns=args.max_turns, timeout_seconds=args.timeout
        )
        factory: SandboxFactory | None = None
        if args.sandbox == "docker":
            factory = DockerSandboxFactory(args.docker_image, allow_network=args.sandbox_network)
        elif args.sandbox == "e2b":
            if not os.environ.get("E2B_API_KEY", "").strip():
                raise ValueError("Set E2B_API_KEY before using the remote sandbox.")
            factory = E2BSandboxFactory(
                os.environ.get("E2B_TEMPLATE", ""),
                allow_network=args.sandbox_network,
                lifetime_seconds=ceil(spec.timeout_seconds) + 60,
            )
        inputs = _input_files(args.input)
        plugins = ()
        if request.plugins:
            from agent_cook.extensions.plugins import load_plugins

            plugins = load_plugins(request.plugins)
        skill_directories = (*request.skill_directories, *(
            plugin.skills_dir for plugin in plugins if plugin.skills_dir is not None
        ))
        skills = None
        if plugins or skill_directories or request.mounted_skills is not None:
            from agent_cook.extensions.skills import SkillCatalog

            skills = SkillCatalog(skill_directories, request.mounted_skills)
        if factory is None and (
            (skills is not None and skills.names) or any(plugin.hooks or plugin.files for plugin in plugins)
        ):
            raise ValueError("Mounted skills, plugin files, and hooks require --sandbox.")
    except (OSError, ValueError) as error:
        parser.error(str(error))

    run_id = uuid4().hex
    trace_path = args.trace or Path(".agent-cook/runs") / f"{run_id}.jsonl"

    try:
        result = asyncio.run(_run(
            spec, request.task, api_key, trace_path, run_id, factory, inputs,
            image_model=image_model.strip() or None, skills=skills, plugins=plugins,
        ))
    except KeyboardInterrupt:
        print("Run cancelled.", file=sys.stderr)
        return 130
    except ModuleNotFoundError as error:
        print(f"Install the project dependencies before running ({error.name}).", file=sys.stderr)
        return 1
    except (AgentRunError, OSError, SandboxError) as error:
        print(f"{error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"The provider or runtime failed ({type(error).__name__}).", file=sys.stderr)
        return 1

    print(result.text)
    print(f"Trace written to {trace_path}", file=sys.stderr)
    for artifact in result.artifacts:
        print(f"Result file {trace_path.parent / artifact.path}", file=sys.stderr)
    return 0
