from dataclasses import asdict, dataclass

from agent_cook.tools.spec import ToolCall, ToolContext, ToolError, ToolResult


@dataclass(frozen=True, slots=True)
class HookSpec:
    plugin: str
    event: str
    tool: str
    script: str
    source: str
    timeout_seconds: float


class HookRunner:
    def __init__(self, hooks: tuple[HookSpec, ...]) -> None:
        self._hooks = hooks

    async def __call__(
        self, event: str, call: ToolCall, arguments: dict[str, object], context: ToolContext,
        result: ToolResult | None,
    ) -> None:
        for hook in self._hooks:
            if (hook.event, hook.tool) != (event, call.name):
                continue
            data = {"plugin": hook.plugin, "script": hook.script, "event": event, "tool_call_id": call.id}
            if context.events is not None:
                context.events.emit("hook.started", **data)
            problem = await self._run(hook, call, arguments, context, result)
            if context.events is not None:
                context.events.emit("hook.finished", **data, is_error=problem is not None, message=problem)
            if problem is not None and event == "before_tool":
                raise ToolError(f"Hook '{hook.script}' blocked '{call.name}': {problem}")

    @staticmethod
    async def _run(
        hook: HookSpec, call: ToolCall, arguments: dict[str, object], context: ToolContext,
        result: ToolResult | None,
    ) -> str | None:
        try:
            payload = {
                "event": hook.event, "tool": call.name, "tool_call_id": call.id,
                "run_id": context.run_id, "arguments": arguments,
                "result": asdict(result) if result is not None else None,
            }
            sandbox = await context.get_sandbox()
            response = await sandbox.request(
                "run_hook", {"source": hook.source, "payload": payload, "timeout_seconds": hook.timeout_seconds},
                timeout_seconds=hook.timeout_seconds + 10,
            )
            if response["timed_out"]:
                return "Hook timed out."
            if response["exit_code"] != 0:
                output = response["stderr"].strip() or response["stdout"].strip()
                return output[:600] or f"Hook exited with code {response['exit_code']}."
        except Exception as error:
            return f"Hook could not run ({type(error).__name__})."
        return None
