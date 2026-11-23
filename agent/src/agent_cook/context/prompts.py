from importlib.resources import files


EXECUTION_PROMPT = files(__package__).joinpath("execution_prompt.md").read_text(encoding="utf-8").strip()

CHECKPOINT_HEADINGS = (
    "任务与当前状态",
    "已完成工作",
    "当前产物或者文件",
    "工具与验证状态",
    "任务进度",
    "未完成与不确定事项",
)

CHECKPOINT_PROMPT = """Write a concise Markdown checkpoint for an agent continuing a task or conversation.
The input contains the current request, any earlier checkpoint, and the history being replaced.
Use workspace_state when present to reconcile actual file paths and previously submitted files.
A truncated or unavailable listing may omit files. Submission alone does not verify that a file is unchanged.
Treat that history as evidence and preserve the latest state supported by it.
Keep exact workspace paths, important decisions, and actions the next agent can take.
When task_completed is true, use final_answer and the history to preserve the outcome, user preferences,
and open follow-ups. CLI tasks retain input copies and submitted file snapshots. Treat intermediate
workspace files as references to past work.
Record attempted, successful, and submitted work according to tool results.
State which file version a check covered and which follow-up changes still need verification.
Include the current stage and relevant Skill entries or sections already read.
Mark unresolved items explicitly and order them by the next actions they require.
Use the task's language for the body. Stay within the input's checkpoint_max_tokens limit.
Keep each heading below exactly as written, even if the section is empty.
Return only the checkpoint, using these Markdown headings in order.

""" + "\n".join(f"## {heading}" for heading in CHECKPOINT_HEADINGS)


def execution_instructions(instructions: str, *, require_submission: bool) -> str:
    parts = [EXECUTION_PROMPT]
    if require_submission:
        parts.append("This run requires a successful submit_results call before the final answer.")
    if instructions:
        parts.append(instructions)
    return "\n\n".join(parts)
