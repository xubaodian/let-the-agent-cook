from agent_cook.context.budget import estimate_messages
from agent_cook.context.policy import RETAINED_USER_TOKEN_LIMIT, ContextPolicy
from agent_cook.state.session import COMPLETION_CHECK_PREFIX, Checkpoint, Message, Session


def build_context(
    instructions: str, session: Session, *, policy: ContextPolicy, checkpoint: Checkpoint | None = None,
    task_start: int | None = 0,
) -> tuple[Message, ...]:
    """Build a fresh view of the session for a model request."""
    system = (Message(role="system", content=instructions),) if instructions else ()
    checkpoint = checkpoint or session.checkpoint
    if checkpoint is None:
        return (*system, *session.messages)
    requests = ()
    if task_start is not None and task_start < checkpoint.covered_messages:
        requests = (session.messages[task_start],)
    elif policy.post_turn_token_limit is not None:
        requests = _retained_user_requests(session.messages, checkpoint.covered_messages)
    history = Message(
        role="user",
        content="Runtime checkpoint from earlier turns. Follow the latest user request and current tool evidence.\n\n"
        + checkpoint.markdown,
    )
    return (*system, *requests, history, *session.messages[checkpoint.covered_messages:])


def _retained_user_requests(history: list[Message], covered: int) -> tuple[Message, ...]:
    """Recover the most recent complete user requests from the stored conversation."""
    remaining = RETAINED_USER_TOKEN_LIMIT
    kept = []
    for index in range(covered - 1, -1, -1):
        message = history[index]
        if message.role != "user" or not message.content or message.content.startswith(COMPLETION_CHECK_PREFIX):
            continue
        tokens = estimate_messages((message,))
        if tokens > remaining:
            break
        kept.append(message)
        remaining -= tokens
    return tuple(reversed(kept))
