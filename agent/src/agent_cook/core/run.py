import asyncio
from collections.abc import Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace

from agent_cook.core.delegation import DELEGATE_TOOL, register_delegate
from agent_cook.core.loop import run_loop
from agent_cook.core.types import AgentRunError, AgentSpec, RunResult, SubagentSpec
from agent_cook.events.journal import RunEvents
from agent_cook.model.client import ModelClient
from agent_cook.sandbox.interface import Sandbox, SandboxFactory
from agent_cook.state.session import Message, Session
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.spec import ToolContext, ToolHook


async def run_agent(
    spec: AgentSpec,
    task: str,
    *,
    model: ModelClient,
    tools: ToolRegistry | None = None,
    sandbox_factory: SandboxFactory | None = None,
    input_files: Mapping[str, bytes] | Callable[[], Mapping[str, bytes]] | None = None,
    events: RunEvents | None = None,
    subagents: Sequence[SubagentSpec] = (),
    hook: ToolHook | None = None,
) -> RunResult:
    """Run a task and own its resources through completion or cancellation.

    A callable input_files is evaluated when the task opens its workspace.
    """
    if not task.strip():
        raise ValueError("A task must contain text.")
    if input_files and sandbox_factory is None:
        raise ValueError("Input files require a task sandbox.")

    events = events if events is not None else RunEvents()
    tools = tools if tools is not None else ToolRegistry()
    if subagents:
        tools = tools.copy()
        register_delegate(tools, subagents, spec, model)
        spec = replace(spec, tool_names=(*spec.tool_names, DELEGATE_TOOL))
    session = Session([Message(role="user", content=task)])
    inputs = dict(input_files) if isinstance(input_files, Mapping) else input_files
    events.emit("run.started", model=spec.model)

    @asynccontextmanager
    async def open_sandbox():
        if sandbox_factory is None:
            raise RuntimeError("This run has no sandbox backend configured.")
        files = await asyncio.to_thread(inputs) if callable(inputs) else inputs
        async with sandbox_factory.open(events.run_id) as environment:
            if files is not None:
                for path, content in files.items():
                    await environment.put_file(path, content)
            yield environment

    try:
        async with AsyncExitStack() as resources:
            sandbox: Sandbox | None = None
            sandbox_lock = asyncio.Lock()

            async def get_sandbox() -> Sandbox:
                nonlocal sandbox
                async with sandbox_lock:
                    if sandbox is None:
                        sandbox = await resources.enter_async_context(open_sandbox())
                    return sandbox

            tool_context = ToolContext(events.run_id, frozenset(spec.tool_names), get_sandbox, events, hook)
            deadline = asyncio.timeout(spec.timeout_seconds)
            try:
                async with deadline:
                    result = await run_loop(
                        spec,
                        session,
                        model=model,
                        tools=tools,
                        tool_context=tool_context,
                        events=events,
                    )
            except TimeoutError as error:
                if deadline.expired():
                    raise AgentRunError(f"The run exceeded {spec.timeout_seconds:g} seconds.") from error
                raise
    except asyncio.CancelledError:
        events.emit("run.cancelled")
        raise
    except AgentRunError as error:
        events.emit("run.failed", error_type=type(error).__name__, message=str(error))
        raise
    except Exception as error:
        events.emit("run.failed", error_type=type(error).__name__)
        raise

    events.emit("run.completed", text=result.text)
    return result
