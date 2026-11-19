from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agent_cook.events.journal import RunEvents
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
class SubmittedFile:
    workspace_path: str
    sha256: str
    artifact: ArtifactRef


@dataclass(frozen=True, slots=True)
class ToolResult:
    model_content: str
    is_error: bool = False
    artifacts: tuple[ArtifactRef, ...] = ()
    ui: UiAttachment | None = None
    submitted_files: tuple[SubmittedFile, ...] = ()


ToolHook = Callable[
    [str, ToolCall, dict[str, object], "ToolContext", ToolResult | None], Awaitable[None],
]


@dataclass(frozen=True, slots=True)
class ToolContext:
    run_id: str
    allowed_tools: frozenset[str]
    get_sandbox: Callable[[], Awaitable[Sandbox]]
    events: RunEvents | None = None
    hook: ToolHook | None = None


class ToolError(RuntimeError):
    """An expected tool error the model can act on."""


ToolHandler = Callable[[dict[str, object], ToolContext], Awaitable[ToolResult]]
