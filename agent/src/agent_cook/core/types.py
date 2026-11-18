from dataclasses import dataclass
from math import isfinite

from agent_cook.protocol.artifacts import ArtifactRef


@dataclass(frozen=True, slots=True)
class AgentSpec:
    model: str
    instructions: str = ""
    tool_names: tuple[str, ...] = ()
    max_model_turns: int = 8
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("A model ID is required.")
        if self.max_model_turns < 1:
            raise ValueError("The model turn limit must be positive.")
        if not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("The run timeout must be a positive, finite duration.")
        if len(set(self.tool_names)) != len(self.tool_names):
            raise ValueError("Each tool can be enabled only once per agent.")


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    text: str
    artifacts: tuple[ArtifactRef, ...] = ()


class AgentRunError(RuntimeError):
    """The model turn or run budget prevented a final answer."""
