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

CHECKPOINT_PROMPT = """Write a concise Markdown checkpoint for an agent continuing the same task.
The input contains the original request, any earlier checkpoint, and the completed history being replaced.
Treat that history as evidence and preserve the latest state supported by it.
Keep exact workspace paths, important decisions, and actions the next agent can take.
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
