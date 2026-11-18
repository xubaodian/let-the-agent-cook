import argparse
import asyncio
import os
import sys
from dataclasses import replace
from math import ceil
from pathlib import Path
from uuid import uuid4

from agent_cook.core.run import run_agent
from agent_cook.core.types import AgentRunError, AgentSpec, RunResult
from agent_cook.events.journal import RunEvents, write_jsonl
from agent_cook.model.openrouter import OPENROUTER_BASE_URL, OpenRouterModel
from agent_cook.sandbox.docker import DEFAULT_DOCKER_IMAGE, DockerSandboxFactory
from agent_cook.sandbox.e2b import E2BSandboxFactory
from agent_cook.sandbox.interface import SandboxError, SandboxFactory
from agent_cook.sandbox.limits import MAX_FILE_BYTES, MAX_TRANSFER_BYTES
from agent_cook.sandbox.paths import workspace_path
from agent_cook.tools.artifacts import ArtifactExporter
from agent_cook.tools.workspace import workspace_tools


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
) -> RunResult:
    from openai import AsyncOpenAI

    registry = None
    if factory is not None:
        exporter = ArtifactExporter(trace_path.parent / f"{run_id}-artifacts")
        registry, tool_names = workspace_tools(exporter)
        spec = replace(spec, tool_names=tool_names)

    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("x", encoding="utf-8") as trace:
        events = RunEvents(lambda event: write_jsonl(trace, event), run_id=run_id)
        async with AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL) as client:
            return await run_agent(
                spec,
                task,
                model=OpenRouterModel(client),
                tools=registry,
                sandbox_factory=factory,
                input_files=inputs,
                events=events,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one task with the OpenRouter model you configure.")
    parser.add_argument("task", help="Text to send to the agent")
    parser.add_argument(
        "--model", default=os.environ.get("OPENROUTER_MODEL"), help="OpenRouter model ID"
    )
    parser.add_argument("--trace", type=Path, help="Write the run to a new JSONL file at this path")
    parser.add_argument("--sandbox", choices=("docker", "e2b"), help="Enable task workspace tools")
    parser.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE, help="Prebuilt local sandbox image")
    parser.add_argument("--sandbox-network", action="store_true", help="Enable outbound sandbox networking")
    parser.add_argument("--input", action="append", type=Path, default=[], help="Copy a local input file into the workspace")
    parser.add_argument("--max-turns", type=int, default=8, help="Maximum number of model turns")
    parser.add_argument("--timeout", type=float, default=120, help="Run time limit in seconds")
    args = parser.parse_args(argv)

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        parser.error("Set OPENROUTER_API_KEY before running the agent.")
    if not args.model or not args.model.strip():
        parser.error("Set OPENROUTER_MODEL or pass --model with an OpenRouter model ID.")
    if not args.task.strip():
        parser.error("Provide a task with at least one non-whitespace character.")

    if (args.input or args.sandbox_network) and args.sandbox is None:
        parser.error("--input and --sandbox-network require --sandbox.")
    try:
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
    except (OSError, ValueError) as error:
        parser.error(str(error))

    run_id = uuid4().hex
    trace_path = args.trace or Path(".agent-cook/runs") / f"{run_id}.jsonl"

    try:
        result = asyncio.run(_run(spec, args.task, api_key, trace_path, run_id, factory, inputs))
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
