from agent_cook.state.session import Message, Session


def build_context(instructions: str, session: Session) -> tuple[Message, ...]:
    """Build a fresh view of the session for a model request."""
    if instructions:
        return (Message(role="system", content=instructions), *session.messages)
    return tuple(session.messages)
