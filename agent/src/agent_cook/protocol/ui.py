from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class UiAttachment:
    """A view kind and its JSON-serializable input for a UI host."""

    kind: str
    data: dict[str, object] = field(default_factory=dict)
