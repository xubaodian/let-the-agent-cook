import asyncio
from collections.abc import Sequence
from typing import Any

from agent_cook.context.policy import DEFAULT_CONTEXT_POLICY
from agent_cook.core.loop import run_loop
from agent_cook.core.types import AgentRunError, AgentSpec, SubagentSpec
from agent_cook.events.journal import RunEvents
from agent_cook.model.client import ModelClient
from agent_cook.state.session import Message, Session
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.spec import ToolContext, ToolDefinition, ToolError, ToolResult

DELEGATE_TOOL = "delegate"


def register_delegate(
    registry: ToolRegistry, subagents: Sequence[SubagentSpec], parent: AgentSpec, model: ModelClient,
) -> None:
    profiles = {profile.name: profile for profile in subagents}
    if len(profiles) != len(subagents):
        raise ValueError("Each subagent name must be unique in the run.")
    descriptions = "\n".join(f"- {profile.name}: {profile.description}" for profile in subagents)
    definition = ToolDefinition(
        DELEGATE_TOOL,
        "Delegate a focused task to a subagent and wait for its result.\n" + descriptions,
        {
            "type": "object",
            "required": ["agent", "task"],
            "properties": {
                "agent": {"type": "string", "enum": list(profiles)},
                "task": {"type": "string", "minLength": 1, "maxLength": 16000},
            },
            "additionalProperties": False,
        },
    )

    async def delegate(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        task = arguments["task"].strip()
        if not task:
            raise ToolError("A delegated task must contain text.")
        profile = profiles[arguments["agent"]]
        names = tuple(
            name for name in profile.tool_names
            if name in context.allowed_tools and name != DELEGATE_TOOL
        )
        model_id = profile.model or parent.model
        spec = AgentSpec(
            model=model_id, instructions=profile.instructions, tool_names=names,
            max_model_turns=min(parent.max_model_turns, profile.max_model_turns),
            context_policy=parent.context_policy if model_id == parent.model else DEFAULT_CONTEXT_POLICY,
            require_submission=False,
        )
        events = (context.events or RunEvents(run_id=context.run_id)).child()
        child_context = ToolContext(events.run_id, frozenset(names), context.get_sandbox, events, context.hook)
        session = Session([Message(role="user", content=task)])
        events.emit("run.started", model=spec.model, agent=profile.name)
        deadline = asyncio.timeout(profile.timeout_seconds)
        try:
            async with deadline:
                result = await run_loop(
                    spec, session, model=model, tools=registry, tool_context=child_context, events=events,
                )
        except asyncio.CancelledError:
            events.emit("run.cancelled")
            raise
        except Exception as error:
            events.emit("run.failed", error_type=type(error).__name__)
            if isinstance(error, TimeoutError) and deadline.expired():
                raise ToolError(f"Subagent '{profile.name}' timed out.") from error
            if isinstance(error, AgentRunError):
                raise ToolError(f"Subagent '{profile.name}' failed. {error}") from error
            raise ToolError(f"Subagent '{profile.name}' failed ({type(error).__name__}).") from error
        events.emit("run.completed", text=result.text)
        return ToolResult(result.text, artifacts=result.artifacts)

    registry.register(definition, delegate)
