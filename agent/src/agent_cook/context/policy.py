from dataclasses import dataclass


INPUT_TOKEN_LIMIT = 360_000
OUTPUT_TOKEN_LIMIT = 50_000
RETAINED_USER_TOKEN_LIMIT = 4_096


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    keep_recent_turns: int = 2
    max_checkpoint_tokens: int = 1_600
    post_turn_token_limit: int | None = None

    def __post_init__(self) -> None:
        if self.keep_recent_turns < 0 or self.max_checkpoint_tokens < 1:
            raise ValueError("Invalid context compaction settings.")
        if (
            self.post_turn_token_limit is not None
            and not self.max_checkpoint_tokens < self.post_turn_token_limit <= INPUT_TOKEN_LIMIT
        ):
            raise ValueError("The post-turn threshold must exceed the checkpoint budget and fit the input limit.")


DEFAULT_CONTEXT_POLICY = ContextPolicy()
CLI_CONTEXT_POLICY = ContextPolicy(post_turn_token_limit=32_000)
