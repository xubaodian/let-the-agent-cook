import json
from collections.abc import Awaitable, Callable, Sequence

from agent_cook.context.budget import PromptBudget, estimate_messages, estimate_text
from agent_cook.context.builder import build_context
from agent_cook.context.policy import INPUT_TOKEN_LIMIT, ContextPolicy
from agent_cook.context.prompts import CHECKPOINT_HEADINGS, CHECKPOINT_PROMPT
from agent_cook.events.journal import RunEvents
from agent_cook.model.client import ModelCallTimeout, ModelClient, model_call_deadline
from agent_cook.state.session import Checkpoint, Message, Session


class ContextLimitError(RuntimeError):
    """The next request could not fit into the input budget."""


class _CheckpointError(ValueError):
    """A checkpoint failure with a reason safe to publish in the event journal."""


async def prepare_context(
    instructions: str,
    session: Session,
    *,
    policy: ContextPolicy,
    budget: PromptBudget,
    model: ModelClient,
    model_id: str,
    events: RunEvents,
    workspace_state: Callable[[], Awaitable[dict[str, object]]] | None = None,
    task_start: int = 0,
    after_task: bool = False,
) -> tuple[Message, ...]:
    phase = "post_turn" if after_task else "in_task"
    active_task = None if after_task else task_start
    limit = INPUT_TOKEN_LIMIT
    if after_task:
        instructions, budget = "", PromptBudget()
        if policy.post_turn_token_limit is None:
            return build_context(instructions, session, policy=policy, task_start=active_task)
        limit = policy.post_turn_token_limit

    messages = build_context(instructions, session, policy=policy, task_start=active_task)
    tokens = budget.estimate(messages)
    if tokens <= limit:
        return messages

    reason = "no_completed_turns"
    boundary = len(session.messages) if after_task else _compression_boundary(
        instructions, session, policy, budget, task_start,
    )
    if boundary is not None:
        events.emit("context.compaction_started", estimated_input_tokens=tokens, covered_messages=boundary, phase=phase)
        workspace = None
        for repair in (False, True):
            if repair:
                if reason == "checkpoint_did_not_fit":
                    boundary = len(session.messages)
                events.emit("context.compaction_retry", reason=reason, covered_messages=boundary, phase=phase)
            try:
                if workspace is None:
                    workspace = await workspace_state() if workspace_state is not None else {}
                checkpoint = await _summarize(
                    session, boundary, policy, model, model_id,
                    workspace=workspace, repair=repair, task_start=task_start, after_task=after_task,
                )
                candidate = build_context(
                    instructions, session, policy=policy, checkpoint=checkpoint, task_start=active_task,
                )
                reduced = budget.estimate(candidate)
                if reduced > limit:
                    raise _CheckpointError("checkpoint_did_not_fit")
            except _CheckpointError as error:
                reason, error_type = str(error), type(error).__name__
                if not repair and reason in ("invalid_checkpoint", "checkpoint_did_not_fit"):
                    continue
            except ModelCallTimeout as error:
                reason, error_type = "checkpoint_model_timeout", type(error).__name__
            except Exception as error:
                reason, error_type = "checkpoint_model_error", type(error).__name__
            else:
                session.checkpoint = checkpoint
                events.emit(
                    "context.compacted", covered_messages=boundary, checkpoint=checkpoint.markdown,
                    estimated_input_tokens_before=tokens, estimated_input_tokens_after=reduced, phase=phase,
                )
                return candidate
            events.emit(
                "context.compaction_failed", covered_messages=boundary, reason=reason, error_type=error_type,
                estimated_input_tokens=tokens, input_budget=limit, phase=phase,
            )
            break

    if after_task:
        return messages
    raise ContextLimitError(
        f"The estimated input ({tokens} tokens) exceeds the {INPUT_TOKEN_LIMIT}-token input budget. "
        f"Context compaction could not continue ({reason.replace('_', ' ')}). "
        "Split the task or keep large tool results in workspace files."
    )


def _compression_boundary(
    instructions: str, session: Session, policy: ContextPolicy, budget: PromptBudget, task_start: int,
) -> int | None:
    start = session.checkpoint.covered_messages if session.checkpoint else 0
    turns = [
        index for index in range(start, len(session.messages))
        if session.messages[index].role == "assistant"
    ]
    if not turns:
        return None

    # Only replace complete turns. Reduce the retained tail when it cannot fit with a checkpoint.
    end = len(session.messages)
    preferred = max(1, len(turns) - policy.keep_recent_turns)
    for boundary in turns[preferred:]:
        candidate = build_context(
            instructions, session, policy=policy, checkpoint=Checkpoint(boundary, ""), task_start=task_start,
        )
        if budget.estimate(candidate) + policy.max_checkpoint_tokens <= INPUT_TOKEN_LIMIT:
            return boundary
    return end


async def _summarize(
    session: Session, end: int, policy: ContextPolicy, model: ModelClient, model_id: str,
    *, workspace: dict[str, object], repair: bool, task_start: int, after_task: bool,
) -> Checkpoint:
    previous = session.checkpoint
    start = previous.covered_messages if previous else 0
    separate = {task_start, len(session.messages) - 1} if after_task else {task_start}
    history = [session.messages[index] for index in range(start, end) if index not in separate]
    for preview in ((4096, 1024, 256) if repair else (None, 4096, 1024, 256)):
        payload = {
            "task": _preview(session.messages[task_start].content, preview),
            "previous_checkpoint": previous.markdown if previous else None,
            "checkpoint_max_tokens": policy.max_checkpoint_tokens,
            "workspace_state": workspace,
            "completed_history": _transcript(history, session.failed_tool_calls, preview),
        }
        if after_task:
            payload["task_completed"] = True
            payload["final_answer"] = _preview(session.messages[-1].content, preview)
        if repair:
            payload["format_repair"] = (
                "The previous checkpoint was unusable. Use all six required headings verbatim in order. "
                "Keep each section short. Preserve the current state and paths. Return plain Markdown."
            )
        messages = (
            Message("system", CHECKPOINT_PROMPT),
            Message("user", json.dumps(payload, ensure_ascii=False)),
        )
        if estimate_messages(messages) <= INPUT_TOKEN_LIMIT:
            break
    else:
        raise _CheckpointError("checkpoint_input_too_large")

    async with model_call_deadline():
        turn = await model.next_turn(model=model_id, messages=messages, tools=())
    markdown = (turn.message.content or "").strip()
    headings = tuple(line[3:].strip() for line in markdown.splitlines() if line.startswith("## "))
    if (
        turn.finish_reason != "stop"
        or turn.message.tool_calls
        or headings != CHECKPOINT_HEADINGS
        or not markdown.startswith(f"## {CHECKPOINT_HEADINGS[0]}\n")
        or estimate_text(markdown) > policy.max_checkpoint_tokens
    ):
        raise _CheckpointError("invalid_checkpoint")
    return Checkpoint(end, markdown)


def _transcript(
    messages: Sequence[Message], failed_tool_calls: set[str], preview: int | None,
) -> list[dict[str, object]]:
    return [
        {
            "role": message.role,
            "content": _preview(message.content, preview),
            "tool_call_id": message.tool_call_id,
            "tool_error": message.tool_call_id in failed_tool_calls if message.role == "tool" else None,
            "tool_calls": [
                {"id": call.id, "name": call.name, "arguments": _preview(call.arguments, preview)}
                for call in message.tool_calls
            ],
        }
        for message in messages
    ]


def _preview(text: str | None, limit: int | None) -> str | None:
    if text is None or limit is None or len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n[Middle omitted for checkpoint input]\n" + text[-half:]
