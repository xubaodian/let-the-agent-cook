import asyncio
from dataclasses import asdict
from functools import partial

from agent_cook.context.budget import PromptBudget
from agent_cook.context.compaction import ContextLimitError, prepare_context
from agent_cook.context.prompts import execution_instructions
from agent_cook.core.types import AgentRunError, AgentSpec, RunResult
from agent_cook.events.journal import RunEvents
from agent_cook.model.client import ModelCallTimeout, ModelClient, model_call_deadline
from agent_cook.protocol.artifacts import ArtifactRef
from agent_cook.sandbox.interface import SandboxError
from agent_cook.state.session import Message, Session
from agent_cook.tools.artifacts import SUBMIT_RESULTS, stale_submissions
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.spec import SubmittedFile, ToolContext


async def run_loop(
    spec: AgentSpec,
    session: Session,
    *,
    model: ModelClient,
    tools: ToolRegistry,
    tool_context: ToolContext,
    events: RunEvents,
    task_start: int = 0,
) -> RunResult:
    definitions = tools.definitions(spec.tool_names)
    require_submission = spec.require_submission
    if require_submission is None:
        require_submission = SUBMIT_RESULTS in spec.tool_names
    if require_submission and SUBMIT_RESULTS not in spec.tool_names:
        raise AgentRunError("Required file delivery needs the submit_results tool.")
    instructions = execution_instructions(spec.instructions, require_submission=require_submission)
    budget = PromptBudget(definitions)
    seen_calls: set[str] = set()
    artifacts: dict[str, ArtifactRef] = {}
    submitted: dict[str, SubmittedFile] = {}
    delivery_artifacts: set[str] = set()
    needs_submission = require_submission
    workspace_state = partial(_checkpoint_workspace, tool_context, submitted)

    turn_number = 0
    while True:
        turn_number += 1
        try:
            messages = await prepare_context(
                instructions, session, policy=spec.context_policy, budget=budget,
                model=model, model_id=spec.model, events=events, workspace_state=workspace_state,
                task_start=task_start,
            )
        except ContextLimitError as error:
            raise AgentRunError(str(error)) from error
        events.emit("model.started", turn=turn_number)
        options = {"web_enabled": True} if spec.web_enabled else {}
        try:
            async with model_call_deadline():
                turn = await model.next_turn(model=spec.model, messages=messages, tools=definitions, **options)
        except ModelCallTimeout as error:
            raise AgentRunError(str(error)) from error
        # The hosted tool transcript contributes to usage but is not sent back by this runtime.
        hosted_context = bool(turn.web_sources) or any(turn.web_usage.values())
        budget.observe(messages, None if hosted_context else turn.prompt_tokens)
        web = {"web_usage": turn.web_usage, "web_sources": list(turn.web_sources)} if hosted_context else {}
        events.emit(
            "model.finished", turn=turn_number, finish_reason=turn.finish_reason, prompt_tokens=turn.prompt_tokens,
            **web,
        )

        if turn.finish_reason not in ("stop", "tool_calls"):
            raise AgentRunError(f"The model stopped before completing its turn ({turn.finish_reason}).")
        message = turn.message
        if not message.tool_calls:
            text = message.content
            if turn.finish_reason == "tool_calls" or text is None or not text.strip():
                raise AgentRunError("The model returned neither a final answer nor valid tool calls.")
            session.messages.append(message)
            feedback = None
            if needs_submission:
                feedback = "Call submit_results to submit the final files before answering."
            elif submitted:
                stale = await stale_submissions(await tool_context.get_sandbox(), submitted.values())
                if stale:
                    for path in stale:
                        del submitted[path]
                    needs_submission = True
                    feedback = (
                        f"These submissions changed or are missing in the workspace: {', '.join(stale)}. "
                        "Verify the current deliverables and call submit_results before answering."
                    )
            if feedback:
                if turn_number == spec.max_model_turns:
                    raise AgentRunError(feedback)
                session.messages.append(Message("user", f"Runtime completion check. {feedback}"))
                events.emit("run.submission_required", turn=turn_number)
                continue
            final_artifacts = tuple(file.artifact for file in submitted.values())
            if not require_submission:
                final_artifacts += tuple(artifact for key, artifact in artifacts.items() if key not in delivery_artifacts)
            if events.parent_run_id is None and spec.context_policy.post_turn_token_limit is not None:
                try:
                    await prepare_context(
                        instructions, session, policy=spec.context_policy, budget=budget,
                        model=model, model_id=spec.model, events=events, workspace_state=workspace_state,
                        task_start=task_start, after_task=True,
                    )
                except asyncio.CancelledError:
                    # The task and its artifacts have passed completion checks.
                    events.emit("context.compaction_failed", phase="post_turn", reason="cancelled")
            return RunResult(tool_context.run_id, text, final_artifacts)

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
            if events.diagnostics is not None:
                events.diagnostics.emit(
                    "tool.call", source_run_id=events.run_id, tool_call_id=call.id,
                    name=call.name, arguments=call.arguments,
                )
            result = await tools.invoke(call, tool_context)
            session.messages.append(
                Message(role="tool", content=result.model_content, tool_call_id=call.id)
            )
            if result.is_error:
                session.failed_tool_calls.add(call.id)
            events.emit("tool.finished", tool_call_id=call.id, name=call.name, is_error=result.is_error)
            if events.diagnostics is not None:
                events.diagnostics.emit(
                    "tool.result", source_run_id=events.run_id, tool_call_id=call.id,
                    name=call.name, output=result.model_content, is_error=result.is_error,
                )
            if call.name == SUBMIT_RESULTS and not result.is_error and result.submitted_files:
                needs_submission = False
                submitted.update((file.workspace_path, file) for file in result.submitted_files)
                delivery_artifacts.update(file.artifact.id for file in result.submitted_files)
            for artifact in result.artifacts:
                previous = artifacts.get(artifact.id)
                if previous is not None and previous != artifact:
                    raise AgentRunError(f"Two different artifacts share the ID '{artifact.id}'.")
                if previous is None:
                    artifacts[artifact.id] = artifact
                    events.emit("artifact.created", tool_call_id=call.id, artifact=asdict(artifact))
            if result.ui is not None:
                events.emit("ui.attached", tool_call_id=call.id, attachment=asdict(result.ui))


async def _checkpoint_workspace(context: ToolContext, submitted: dict[str, SubmittedFile]) -> dict[str, object]:
    limit = 80
    state: dict[str, object] = {}
    if submitted:
        state["submitted_paths"] = list(submitted)[-limit:]
        state["submitted_paths_truncated"] = len(submitted) > limit
    sandbox = context.current_sandbox() if context.current_sandbox and "ls" in context.allowed_tools else None
    if sandbox is None:
        return state
    try:
        async with asyncio.timeout(5):
            listing = await sandbox.request(
                "list_files", {"recursive": True, "kind": "file", "max_results": limit}, timeout_seconds=3,
            )
        paths = [entry["path"] for entry in listing["entries"]]
        truncated = listing["truncated"]
    except (SandboxError, OSError, TimeoutError, KeyError, TypeError):
        state["workspace_listing_unavailable"] = True
    else:
        state.update(workspace_files=paths, workspace_files_truncated=truncated)
    return state
