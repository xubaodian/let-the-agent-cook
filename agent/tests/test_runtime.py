import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from agent_cook.context.prompts import EXECUTION_PROMPT
from agent_cook.core.run import run_agent
from agent_cook.core.types import AgentRunError, AgentSpec, SubagentSpec
from agent_cook.events.journal import RunEvents
from agent_cook.model.client import ModelTurn
from agent_cook.protocol.artifacts import ArtifactRef
from agent_cook.protocol.ui import UiAttachment
from agent_cook.state.session import Message
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.spec import ToolCall, ToolDefinition, ToolResult


def tool_definition() -> ToolDefinition:
    return ToolDefinition(
        "fixture",
        "Return a fixture value.",
        {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )


def tool_turn(*calls: ToolCall, finish_reason: str = "tool_calls") -> ModelTurn:
    return ModelTurn(Message(role="assistant", tool_calls=calls), finish_reason)


class ScriptedModel:
    def __init__(self, *turns: ModelTurn) -> None:
        self.next_turn = AsyncMock(side_effect=turns)


class SandboxFixture:
    def __init__(self) -> None:
        self.resource = object()
        self.opened = []
        self.closed = []

    @asynccontextmanager
    async def open(self, run_id):
        self.opened.append(run_id)
        try:
            yield self.resource
        finally:
            self.closed.append(run_id)


class RuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_text_run_does_not_open_a_sandbox(self):
        model = ScriptedModel(ModelTurn(Message("assistant", "Ready."), "stop"))
        sandbox = SandboxFixture()
        records = []

        result = await run_agent(
            AgentSpec(model="fixture/model", instructions="Reply briefly."),
            "Hello",
            model=model,
            sandbox_factory=sandbox,
            events=RunEvents(records.append, run_id="text-run"),
        )

        self.assertEqual(result.text, "Ready.")
        self.assertEqual(result.run_id, "text-run")
        self.assertEqual(sandbox.opened, [])
        self.assertEqual(model.next_turn.call_args.kwargs["tools"], ())
        self.assertEqual(
            model.next_turn.call_args.kwargs["messages"],
            (Message("system", f"{EXECUTION_PROMPT}\n\nReply briefly."), Message("user", "Hello")),
        )
        self.assertEqual([event.seq for event in records], [1, 2, 3, 4])
        self.assertEqual([event.type for event in records], [
            "run.started", "model.started", "model.finished", "run.completed",
        ])

    async def test_tool_results_keep_call_order_and_share_a_sandbox(self):
        first = ToolCall("call-one", "fixture", '{"value":"one"}')
        second = ToolCall("call-two", "fixture", '{"value":"two"}')
        model = ScriptedModel(tool_turn(first, second), ModelTurn(Message("assistant", "Done."), "stop"))
        sandbox = SandboxFixture()
        records = []
        artifact = ArtifactRef("result", "result.txt", "text/plain")

        async def fixture(args, context):
            self.assertIs(await context.get_sandbox(), sandbox.resource)
            return ToolResult(
                str(args["value"]),
                artifacts=(artifact,),
                ui=UiAttachment("text", {"artifact_id": artifact.id}),
            )

        registry = ToolRegistry()
        registry.register(tool_definition(), fixture)
        result = await run_agent(
            AgentSpec("fixture/model", tool_names=("fixture",)),
            "Complete the fixture",
            model=model,
            tools=registry,
            sandbox_factory=sandbox,
            events=RunEvents(records.append, run_id="tool-run"),
        )

        messages = model.next_turn.call_args.kwargs["messages"]
        self.assertEqual(messages[-3].tool_calls, (first, second))
        self.assertEqual([(msg.tool_call_id, msg.content) for msg in messages[-2:]], [
            ("call-one", "one"), ("call-two", "two"),
        ])
        self.assertEqual(sandbox.opened, ["tool-run"])
        self.assertEqual(sandbox.closed, ["tool-run"])
        self.assertEqual(result.artifacts, (artifact,))
        self.assertEqual(sum(event.type == "artifact.created" for event in records), 1)
        self.assertEqual(sum(event.type == "ui.attached" for event in records), 2)

    async def test_bad_arguments_and_disabled_tools_do_not_reach_handlers(self):
        registry = ToolRegistry()
        enabled = AsyncMock()
        disabled = AsyncMock()
        registry.register(tool_definition(), enabled)
        registry.register(
            ToolDefinition("disabled", "A disabled fixture.", {"type": "object"}), disabled
        )
        model = ScriptedModel(
            tool_turn(ToolCall("invalid", "fixture", '{}'), ToolCall("disabled-call", "disabled", '{}')),
            ModelTurn(Message("assistant", "Both calls were rejected."), "stop"),
        )

        await run_agent(
            AgentSpec("fixture/model", tool_names=("fixture",)), "Check", model=model, tools=registry
        )

        enabled.assert_not_awaited()
        disabled.assert_not_awaited()
        messages = model.next_turn.call_args.kwargs["messages"]
        self.assertEqual([msg.tool_call_id for msg in messages[-2:]], ["invalid", "disabled-call"])
        self.assertIn("schema validation", messages[-2].content)
        self.assertIn("unavailable", messages[-1].content)

    async def test_invalid_model_turns_are_never_dispatched(self):
        call = ToolCall("same-id", "fixture", '{"value":"one"}')
        cases = [
            (tool_turn(call, finish_reason="length"), ("fixture",), 2, "stopped"),
            (tool_turn(call, call), ("fixture",), 2, "duplicate"),
            (tool_turn(call), ("fixture",), 1, "budget"),
            (tool_turn(call), (), 2, "no enabled tools"),
        ]
        for turn, names, limit, error in cases:
            with self.subTest(error=error):
                handler = AsyncMock()
                registry = ToolRegistry()
                registry.register(tool_definition(), handler)
                records = []
                with self.assertRaisesRegex(AgentRunError, error):
                    await run_agent(
                        AgentSpec("fixture/model", tool_names=names, max_model_turns=limit),
                        "Check",
                        model=ScriptedModel(turn),
                        tools=registry,
                        events=RunEvents(records.append),
                    )
                handler.assert_not_awaited()
                self.assertEqual(records[-1].type, "run.failed")

    async def test_cancellation_closes_an_acquired_sandbox(self):
        sandbox = SandboxFixture()
        in_handler = asyncio.Event()
        records = []

        async def fixture(_, context):
            await context.get_sandbox()
            in_handler.set()
            await asyncio.Event().wait()

        registry = ToolRegistry()
        registry.register(tool_definition(), fixture)
        model = ScriptedModel(tool_turn(ToolCall("wait", "fixture", '{"value":"hold"}')))
        task = asyncio.create_task(run_agent(
            AgentSpec("fixture/model", tool_names=("fixture",)),
            "Wait",
            model=model,
            tools=registry,
            sandbox_factory=sandbox,
            events=RunEvents(records.append, run_id="cancel-run"),
        ))
        await asyncio.wait_for(in_handler.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(sandbox.closed, ["cancel-run"])
        self.assertEqual(records[-1].type, "run.cancelled")

    async def test_timeout_is_reported_as_a_failed_run(self):
        waiting_model = AsyncMock()

        async def wait(**_):
            await asyncio.Event().wait()

        waiting_model.next_turn.side_effect = wait
        records = []
        with self.assertRaisesRegex(AgentRunError, "exceeded"):
            await run_agent(
                AgentSpec("fixture/model", timeout_seconds=0.01),
                "Wait",
                model=waiting_model,
                events=RunEvents(records.append),
            )
        self.assertEqual(records[-1].type, "run.failed")

    async def test_subagent_timeout_returns_an_error_to_the_parent(self):
        model = AsyncMock()
        records = []

        async def next_turn(messages, **_):
            if messages[1].content == "Check this":
                await asyncio.Event().wait()
            if messages[-1].role == "user":
                return tool_turn(ToolCall(
                    "delegate-call", "delegate", '{"agent":"reviewer","task":"Check this"}',
                ))
            self.assertEqual(messages[-1].role, "tool")
            self.assertIn("Subagent 'reviewer' timed out", messages[-1].content)
            return ModelTurn(Message("assistant", "I can continue."), "stop")

        model.next_turn.side_effect = next_turn
        registry = ToolRegistry()
        result = await run_agent(
            AgentSpec("fixture/model"),
            "Review",
            model=model,
            tools=registry,
            subagents=(SubagentSpec("reviewer", "Reviews content.", "Review only.", timeout_seconds=0.01),),
            events=RunEvents(records.append, run_id="parent"),
        )
        self.assertEqual(result.text, "I can continue.")
        self.assertEqual(records[-1].type, "run.completed")
        failure = next(event for event in records if event.type == "run.failed")
        self.assertEqual(failure.parent_run_id, "parent")
        with self.assertRaisesRegex(ValueError, "no registered handler"):
            registry.definitions(("delegate",))
