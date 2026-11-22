from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    context_window: int = 32_768
    output_reserve: int = 4_096
    safety_margin: int = 1_024
    compact_at: float = 0.8
    keep_recent_turns: int = 2
    max_checkpoint_tokens: int = 1_600

    def __post_init__(self) -> None:
        if self.output_reserve < 1 or self.safety_margin < 0 or self.input_budget < 1:
            raise ValueError("The context window must leave room after the output and safety reserves.")
        if not 0 < self.compact_at <= 1 or self.keep_recent_turns < 0 or self.max_checkpoint_tokens < 1:
            raise ValueError("Invalid context compaction settings.")

    def for_model(self, window: int) -> "ContextPolicy":
        """Fit the reserved space into a smaller published model window."""
        return replace(
            self, context_window=window,
            output_reserve=min(self.output_reserve, max(1, window // 8)),
            safety_margin=min(self.safety_margin, window // 32),
        )

    @property
    def input_budget(self) -> int:
        return self.context_window - self.output_reserve - self.safety_margin

    @property
    def compaction_threshold(self) -> int:
        return max(1, int(self.input_budget * self.compact_at))


DEFAULT_CONTEXT_POLICY = ContextPolicy()
