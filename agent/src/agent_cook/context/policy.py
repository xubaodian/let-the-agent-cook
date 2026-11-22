from dataclasses import dataclass


INPUT_TOKEN_LIMIT = 360_000
OUTPUT_TOKEN_LIMIT = 50_000


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    keep_recent_turns: int = 2
    max_checkpoint_tokens: int = 1_600

    def __post_init__(self) -> None:
        if self.keep_recent_turns < 0 or self.max_checkpoint_tokens < 1:
            raise ValueError("Invalid context compaction settings.")


DEFAULT_CONTEXT_POLICY = ContextPolicy()
