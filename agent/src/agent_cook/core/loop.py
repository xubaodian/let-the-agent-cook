from dataclasses import asdict

from agent_cook.context.builder import build_context
from agent_cook.core.types import AgentRunError, AgentSpec, RunResult
from agent_cook.events.journal import RunEvents
from agent_cook.model.client import ModelClient
from agent_cook.protocol.artifacts import ArtifactRef
from agent_cook.state.session import Message, Session
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.spec import ToolContext


async def run_loop(
    spec: AgentSpec,
    session: Session,
    *,
    model: ModelClient,
    tools: ToolRegistry,
    tool_context: ToolContext,
    events: RunEvents,
) -> RunResult:
    definitions = tools.definitions(spec.tool_names)
    seen_calls: set[str] = set()
    artifacts: dict[str, ArtifactRef] = {}

    for turn_number in range(1, spec.max_model_turns + 1):
        events.emit("model.started", turn=turn_number)
        turn = await model.next_turn(
            model=spec.model,
            messages=build_context(spec.instructions, session),
            tools=definitions,
        )
        events.emit("model.finished", turn=turn_number, finish_reason=turn.finish_reason)

        if turn.finish_reason not in ("stop", "tool_calls"):
            raise AgentRunError(f"The model stopped before completing its turn ({turn.finish_reason}).")
        message = turn.message
        if not message.tool_calls:
            text = message.content
            if turn.finish_reason == "tool_calls" or text is None or not text.strip():
                raise AgentRunError("The model returned neither a final answer nor valid tool calls.")
            session.messages.append(message)
            return RunResult(tool_context.run_id, text, tuple(artifacts.values()))

        if not definitions:
            raise AgentRunError("The model requested a tool while the run had no enabled tools.")
        call_ids = {call.id for call in message.tool_calls}
        if "" in call_ids or len(call_ids) != len(message.tool_calls) or seen_calls & call_ids:
            raise AgentRunError("The model returned missing or duplicate tool call IDs.")
        if turn_number == spec.max_model_turns:
            raise AgentRunError("The model turn budget ran out before the requested tools could run.")

        session.messages.append(message)
        seen_calls.update(call_ids)
        for call in message.tool_calls:
            events.emit("tool.started", tool_call_id=call.id, name=call.name)
            result = await tools.invoke(call, tool_context)
            session.messages.append(
                Message(role="tool", content=result.model_content, tool_call_id=call.id)
            )
            events.emit("tool.finished", tool_call_id=call.id, name=call.name, is_error=result.is_error)
            for artifact in result.artifacts:
                previous = artifacts.get(artifact.id)
                if previous is not None and previous != artifact:
                    raise AgentRunError(f"Two different artifacts share the ID '{artifact.id}'.")
                if previous is None:
                    artifacts[artifact.id] = artifact
                    events.emit("artifact.created", tool_call_id=call.id, artifact=asdict(artifact))
            if result.ui is not None:
                events.emit("ui.attached", tool_call_id=call.id, attachment=asdict(result.ui))

    raise AgentRunError("The model turn budget was exhausted.")
