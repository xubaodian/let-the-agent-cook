from dataclasses import dataclass
from math import isfinite

from agent_cook.context.policy import DEFAULT_CONTEXT_POLICY, ContextPolicy
from agent_cook.protocol.artifacts import ArtifactRef

DEFAULT_MAX_MODEL_TURNS = 300


@dataclass(frozen=True, slots=True)
class AgentSpec:
    model: str
    instructions: str = ""
    tool_names: tuple[str, ...] = ()
    max_model_turns: int | None = DEFAULT_MAX_MODEL_TURNS
    context_policy: ContextPolicy = DEFAULT_CONTEXT_POLICY
    require_submission: bool | None = None
    web_enabled: bool = False

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("A model ID is required.")
        if self.max_model_turns is not None and (type(self.max_model_turns) is not int or self.max_model_turns < 1):
            raise ValueError("The model turn limit must be a positive integer.")
        if len(set(self.tool_names)) != len(self.tool_names):
            raise ValueError("Each tool can be enabled only once per agent.")


@dataclass(frozen=True, slots=True)
class SubagentSpec:
    name: str
    description: str
    instructions: str
    tool_names: tuple[str, ...] = ()
    model: str | None = None
    max_model_turns: int | None = None
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.name, self.description, self.instructions)):
            raise ValueError("A subagent requires a name, description, and instructions.")
        if self.model is not None and not self.model.strip():
            raise ValueError("A subagent model ID must contain text.")
        if self.max_model_turns is not None and (type(self.max_model_turns) is not int or self.max_model_turns < 1):
            raise ValueError("The subagent turn limit must be a positive integer.")
        if self.timeout_seconds is not None and (not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0):
            raise ValueError("The delegate timeout must be a positive, finite duration.")
        if len(set(self.tool_names)) != len(self.tool_names):
            raise ValueError("Each tool can be enabled only once per subagent.")


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    text: str
    artifacts: tuple[ArtifactRef, ...] = ()


class AgentRunError(RuntimeError):
    """An execution limit or invalid model response prevented a final answer."""
