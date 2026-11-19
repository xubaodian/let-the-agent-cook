from agent_cook.state.session import Checkpoint, Message, Session


def build_context(
    instructions: str, session: Session, *, checkpoint: Checkpoint | None = None,
) -> tuple[Message, ...]:
    """Build a fresh view of the session for a model request."""
    system = (Message(role="system", content=instructions),) if instructions else ()
    checkpoint = checkpoint or session.checkpoint
    if checkpoint is None:
        return (*system, *session.messages)
    history = Message(
        role="user",
        content="Runtime checkpoint from earlier turns. Use current tool evidence and the original task.\n\n"
        + checkpoint.markdown,
    )
    return (*system, *session.messages[:1], history, *session.messages[checkpoint.covered_messages:])
