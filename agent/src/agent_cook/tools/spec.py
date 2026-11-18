from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agent_cook.protocol.artifacts import ArtifactRef
from agent_cook.protocol.ui import UiAttachment
from agent_cook.sandbox.interface import Sandbox


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, object]


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    model_content: str
    is_error: bool = False
    artifacts: tuple[ArtifactRef, ...] = ()
    ui: UiAttachment | None = None


@dataclass(frozen=True, slots=True)
class ToolContext:
    run_id: str
    allowed_tools: frozenset[str]
    get_sandbox: Callable[[], Awaitable[Sandbox]]


class ToolError(RuntimeError):
    """An expected tool error the model can act on."""


ToolHandler = Callable[[dict[str, object], ToolContext], Awaitable[ToolResult]]
