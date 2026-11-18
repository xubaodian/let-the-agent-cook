import argparse
import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

from agent_cook.core.run import run_agent
from agent_cook.core.types import AgentRunError, AgentSpec, RunResult
from agent_cook.events.journal import RunEvents, write_jsonl
from agent_cook.model.openrouter import OPENROUTER_BASE_URL, OpenRouterModel


async def _run(spec: AgentSpec, task: str, api_key: str, trace_path: Path, run_id: str) -> RunResult:
    from openai import AsyncOpenAI

    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("x", encoding="utf-8") as trace:
        events = RunEvents(lambda event: write_jsonl(trace, event), run_id=run_id)
        async with AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL) as client:
            return await run_agent(spec, task, model=OpenRouterModel(client), events=events)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one task with the OpenRouter model you configure.")
    parser.add_argument("task", help="Text to send to the agent")
    parser.add_argument(
        "--model", default=os.environ.get("OPENROUTER_MODEL"), help="OpenRouter model ID"
    )
    parser.add_argument("--trace", type=Path, help="Write the run to a new JSONL file at this path")
    args = parser.parse_args(argv)

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        parser.error("Set OPENROUTER_API_KEY before running the agent.")
    if not args.model or not args.model.strip():
        parser.error("Set OPENROUTER_MODEL or pass --model with an OpenRouter model ID.")
    if not args.task.strip():
        parser.error("Provide a task with at least one non-whitespace character.")

    spec = AgentSpec(model=args.model.strip())
    run_id = uuid4().hex
    trace_path = args.trace or Path(".agent-cook/runs") / f"{run_id}.jsonl"

    try:
        result = asyncio.run(_run(spec, args.task, api_key, trace_path, run_id))
    except KeyboardInterrupt:
        print("Run cancelled.", file=sys.stderr)
        return 130
    except ModuleNotFoundError as error:
        print(f"Install the project dependencies before running ({error.name}).", file=sys.stderr)
        return 1
    except (AgentRunError, OSError) as error:
        print(f"{error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"The provider or runtime failed ({type(error).__name__}).", file=sys.stderr)
        return 1

    print(result.text)
    print(f"Trace written to {trace_path}", file=sys.stderr)
    return 0
