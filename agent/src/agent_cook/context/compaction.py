import json
from collections.abc import Sequence

from agent_cook.context.budget import PromptBudget, estimate_messages, estimate_text
from agent_cook.context.builder import build_context
from agent_cook.context.policy import ContextPolicy
from agent_cook.context.prompts import CHECKPOINT_HEADINGS, CHECKPOINT_PROMPT
from agent_cook.events.journal import RunEvents
from agent_cook.model.client import ModelCallTimeout, ModelClient, model_call_deadline
from agent_cook.state.session import Checkpoint, Message, Session


class ContextLimitError(RuntimeError):
    """The next request could not fit into its configured input budget."""


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
) -> tuple[Message, ...]:
    messages = build_context(instructions, session)
    tokens = budget.estimate(messages)
    if tokens < policy.compaction_threshold:
        return messages

    over_budget = tokens > policy.input_budget
    reason = "no_completed_turns"
    boundary = _compression_boundary(instructions, session, policy, budget, tokens)
    if boundary is not None:
        events.emit("context.compaction_started", estimated_input_tokens=tokens, covered_messages=boundary)
        for repair in (False, True):
            if repair:
                if reason == "checkpoint_did_not_fit":
                    boundary = len(session.messages)
                events.emit("context.compaction_retry", reason=reason, covered_messages=boundary)
            try:
                checkpoint = await _summarize(session, boundary, policy, model, model_id, repair=repair)
                candidate = build_context(instructions, session, checkpoint=checkpoint)
                reduced = budget.estimate(candidate)
                if reduced >= tokens or reduced > policy.input_budget:
                    raise _CheckpointError("checkpoint_did_not_fit")
            except _CheckpointError as error:
                reason, error_type = str(error), type(error).__name__
                if over_budget and not repair and reason in ("invalid_checkpoint", "checkpoint_did_not_fit"):
                    continue
            except ModelCallTimeout as error:
                reason, error_type = "checkpoint_model_timeout", type(error).__name__
            except Exception as error:
                reason, error_type = "checkpoint_model_error", type(error).__name__
            else:
                session.checkpoint = checkpoint
                events.emit(
                    "context.compacted", covered_messages=boundary, checkpoint=checkpoint.markdown,
                    estimated_input_tokens_before=tokens, estimated_input_tokens_after=reduced,
                )
                return candidate
            events.emit(
                "context.compaction_failed", covered_messages=boundary, reason=reason, error_type=error_type,
                estimated_input_tokens=tokens, input_budget=policy.input_budget,
            )
            break

    if over_budget:
        raise ContextLimitError(
            f"The estimated input ({tokens} tokens) exceeds the configured budget "
            f"({policy.input_budget} tokens). Context compaction could not continue ({reason.replace('_', ' ')}). "
            "Use --context-window to match the selected model or give the agent smaller inputs."
        )
    return messages


def _compression_boundary(
    instructions: str, session: Session, policy: ContextPolicy, budget: PromptBudget, tokens: int,
) -> int | None:
    start = session.checkpoint.covered_messages if session.checkpoint else 1
    turns = [
        index for index in range(start, len(session.messages))
        if session.messages[index].role == "assistant"
    ]
    if not turns or (len(turns) <= policy.keep_recent_turns and tokens <= policy.input_budget):
        return None

    # Only replace complete turns. Reduce the retained tail when it cannot fit with a checkpoint.
    end = len(session.messages)
    preferred = max(1, len(turns) - policy.keep_recent_turns)
    for boundary in turns[preferred:]:
        candidate = build_context(instructions, session, checkpoint=Checkpoint(boundary, ""))
        if budget.estimate(candidate) + policy.max_checkpoint_tokens <= policy.input_budget:
            return boundary
    return end


async def _summarize(
    session: Session, end: int, policy: ContextPolicy, model: ModelClient, model_id: str, *, repair: bool,
) -> Checkpoint:
    previous = session.checkpoint
    start = previous.covered_messages if previous else 1
    for preview in ((4096, 1024, 256) if repair else (None, 4096, 1024, 256)):
        payload = {
            "task": session.messages[0].content,
            "previous_checkpoint": previous.markdown if previous else None,
            "checkpoint_max_tokens": policy.max_checkpoint_tokens,
            "completed_history": _transcript(session.messages[start:end], session.failed_tool_calls, preview),
        }
        if repair:
            payload["format_repair"] = (
                "The previous checkpoint was unusable. Use all six required headings verbatim in order. "
                "Keep each section short. Preserve the current state and paths. Return plain Markdown."
            )
        messages = (
            Message("system", CHECKPOINT_PROMPT),
            Message("user", json.dumps(payload, ensure_ascii=False)),
        )
        if estimate_messages(messages) <= policy.input_budget:
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
