from agent_cook.state.session import Checkpoint, Message, Session


def build_context(
    instructions: str, session: Session, *, checkpoint: Checkpoint | None = None,
    task_start: int | None = 0,
) -> tuple[Message, ...]:
    """Build a fresh view of the session for a model request."""
    system = (Message(role="system", content=instructions),) if instructions else ()
    checkpoint = checkpoint or session.checkpoint
    if checkpoint is None:
        return (*system, *session.messages)
    task = ()
    if task_start is not None and task_start < checkpoint.covered_messages:
        task = (session.messages[task_start],)
    history = Message(
        role="user",
        content="Runtime checkpoint from earlier turns. Follow the latest user request and current tool evidence.\n\n"
        + checkpoint.markdown,
    )
    return (*system, *task, history, *session.messages[checkpoint.covered_messages:])
