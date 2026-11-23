import asyncio
import json
import unittest
from unittest.mock import AsyncMock

from agent_cook.context.budget import PromptBudget, estimate_messages
from agent_cook.context.policy import CLI_CONTEXT_POLICY, INPUT_TOKEN_LIMIT
from agent_cook.context.prompts import CHECKPOINT_HEADINGS, EXECUTION_PROMPT
from agent_cook.core.run import run_agent
from agent_cook.core.types import AgentRunError, AgentSpec
from agent_cook.events.journal import RunEvents
from agent_cook.model.client import ModelTurn
from agent_cook.state.session import Message, Session
from agent_cook.tools.registry import ToolRegistry
from agent_cook.tools.spec import ToolCall, ToolDefinition, ToolResult
from test_runtime import ScriptedModel


_CHECKPOINT = "\n\n".join(
    f"## {heading}\n{text}"
    for heading, text in zip(CHECKPOINT_HEADINGS, (
        "Build the signup page. Mobile verification is pending.",
        "- Created the form and success state.",
        "- /workspace/site/index.html is the entry point.",
        "- Desktop preview preceded the latest button change.",
        "- Read the page Skill entry.",
        "- Check the current mobile layout and submit.",
    ), strict=True)
)
_FIXTURE = ToolDefinition("fixture", "Return an observation.", {"type": "object"})


def _call(number: int, *, prompt_tokens: int | None = None) -> ModelTurn:
    message = Message(
        "assistant", tool_calls=(ToolCall(f"call-{number}", "fixture", "{}"),),
        provider_data={"reasoning_details": [{"data": f"continuation-{number}"}]},
    )
    return ModelTurn(message, "tool_calls", prompt_tokens=prompt_tokens)


class ContextCompactionTest(unittest.IsolatedAsyncioTestCase):
    def test_budget_switches_between_full_estimates_and_usage_deltas(self):
        self.assertGreaterEqual(len(EXECUTION_PROMPT.split()), 1000)
        self.assertLessEqual(len(EXECUTION_PROMPT.split()), 2000)
        previous = (Message("system", "Instructions."), Message("user", "Initial task."))
        suffix = (_call(1).message, Message("tool", "New data.", tool_call_id="call-1"))
        budget = PromptBudget((_FIXTURE,))
        self.assertGreater(budget.estimate(previous), estimate_messages(previous))
        budget.observe(previous, 500)
        self.assertEqual(budget.estimate(previous + suffix), 500 + estimate_messages(suffix))

        edited = (previous[0], Message("user", "Changed task input."))
        self.assertEqual(budget.estimate(edited), PromptBudget((_FIXTURE,)).estimate(edited))
        budget.observe(edited, None)
        self.assertEqual(budget.estimate(edited + suffix), PromptBudget((_FIXTURE,)).estimate(edited + suffix))

    async def test_usage_compacts_incrementally_and_keeps_complete_recent_turns(self):
        registry = ToolRegistry()
        observed = [ToolResult("Observed.")] * 3
        handler = AsyncMock(side_effect=[ToolResult("Could not inspect it.", is_error=True), *observed])
        registry.register(_FIXTURE, handler)
        summary = ModelTurn(Message("assistant", _CHECKPOINT), "stop", prompt_tokens=200)
        model = ScriptedModel(
            _call(1, prompt_tokens=2000), _call(2, prompt_tokens=2500), _call(3, prompt_tokens=INPUT_TOKEN_LIMIT),
            summary, _call(4, prompt_tokens=INPUT_TOKEN_LIMIT), summary, ModelTurn(Message("assistant", "Done."), "stop"),
        )
        records = []

        result = await run_agent(
            AgentSpec("fixture/model", tool_names=(_FIXTURE.name,), max_model_turns=5, web_enabled=True),
            "Build the signup page.",
            model=model, tools=registry, events=RunEvents(records.append),
        )

        self.assertEqual(result.text, "Done.")
        self.assertEqual(handler.await_count, 4)
        sent = [call.kwargs for call in model.next_turn.call_args_list]
        first_summary = json.loads(sent[3]["messages"][1].content)
        second_summary = json.loads(sent[5]["messages"][1].content)
        self.assertEqual(sent[3]["tools"], ())
        self.assertEqual(sent[5]["tools"], ())
        self.assertNotIn("web_enabled", sent[3])
        self.assertNotIn("web_enabled", sent[5])
        self.assertTrue(all(sent[index]["web_enabled"] for index in (0, 1, 2, 4, 6)))
        self.assertIsNone(first_summary["previous_checkpoint"])
        self.assertEqual(first_summary["completed_history"][-1]["tool_call_id"], "call-1")
        self.assertTrue(first_summary["completed_history"][-1]["tool_error"])
        self.assertEqual(second_summary["previous_checkpoint"], _CHECKPOINT)
        self.assertEqual(second_summary["completed_history"][-1]["tool_call_id"], "call-2")
        self.assertFalse(second_summary["completed_history"][-1]["tool_error"])
        self.assertEqual(len(second_summary["completed_history"]), 2)
        for request, first, second in ((sent[4], 2, 3), (sent[6], 3, 4)):
            system, task, checkpoint, turn, result_one, next_turn, result_two = request["messages"]
            self.assertEqual(system.content, EXECUTION_PROMPT)
            self.assertEqual(task, Message("user", "Build the signup page."))
            self.assertTrue(checkpoint.content.endswith(_CHECKPOINT))
            self.assertEqual((turn, next_turn), (_call(first).message, _call(second).message))
            self.assertEqual((result_one.tool_call_id, result_two.tool_call_id), (f"call-{first}", f"call-{second}"))
            self.assertEqual(request["tools"], (_FIXTURE,))
        compacted = [event.data for event in records if event.type == "context.compacted"]
        self.assertEqual([item["covered_messages"] for item in compacted], [3, 5])
        self.assertTrue(all(item["checkpoint"] == _CHECKPOINT for item in compacted))
        self.assertTrue(all(item["estimated_input_tokens_before"] > INPUT_TOKEN_LIMIT for item in compacted))

    async def test_request_at_the_input_limit_does_not_compact(self):
        observation = Message("tool", "Keep this observation.", tool_call_id="call-1")
        usage = 360_000 - estimate_messages((_call(1).message, observation))
        registry = ToolRegistry()
        registry.register(_FIXTURE, AsyncMock(return_value=ToolResult(observation.content)))
        model = ScriptedModel(_call(1, prompt_tokens=usage), ModelTurn(Message("assistant", "Done."), "stop"))
        records = []

        await run_agent(
            AgentSpec("fixture/model", tool_names=(_FIXTURE.name,)), "Complete the task.",
            model=model, tools=registry, events=RunEvents(records.append),
        )

        self.assertEqual(model.next_turn.await_count, 2)
        last_request = model.next_turn.call_args.kwargs["messages"]
        self.assertEqual(last_request[-2:], (_call(1).message, observation))
        self.assertFalse(any(event.type.startswith("context.compact") for event in records))

    async def test_over_budget_run_recovers_from_an_invalid_checkpoint_once(self):
        observation = Message("tool", "Saved the current slides.\n" + "A" * 180_000, tool_call_id="call-1")
        usage = 360_001 - estimate_messages((_call(1).message, observation))
        invalid = ModelTurn(Message("assistant", "A summary without the required sections."), "stop")
        valid = ModelTurn(Message("assistant", _CHECKPOINT), "stop")
        for repaired in (True, False):
            with self.subTest(repaired=repaired):
                registry = ToolRegistry()
                registry.register(_FIXTURE, AsyncMock(return_value=ToolResult(observation.content)))
                model = ScriptedModel(
                    _call(1, prompt_tokens=usage), invalid, valid if repaired else invalid,
                    ModelTurn(Message("assistant", "Done."), "stop"),
                )
                records = []
                run = run_agent(
                    AgentSpec("fixture/model", tool_names=(_FIXTURE.name,)),
                    "Build the deck.", model=model, tools=registry, events=RunEvents(records.append),
                )
                if repaired:
                    self.assertEqual((await run).text, "Done.")
                    self.assertEqual(model.next_turn.await_count, 4)
                else:
                    with self.assertRaisesRegex(AgentRunError, "invalid checkpoint"):
                        await run
                    self.assertEqual(model.next_turn.await_count, 3)
                started = next(event for event in records if event.type == "context.compaction_started")
                self.assertEqual(started.data["estimated_input_tokens"], 360_001)
                attempts = [event for event in records if event.type == "context.compaction_retry"]
                self.assertEqual([event.data["reason"] for event in attempts], ["invalid_checkpoint"])
                repair = json.loads(model.next_turn.call_args_list[2].kwargs["messages"][-1].content)
                self.assertEqual(repair["task"], "Build the deck.")
                self.assertIn("format_repair", repair)
                self.assertTrue(repair["completed_history"][-1]["content"].startswith("Saved the current slides."))
                self.assertIn("Middle omitted", repair["completed_history"][-1]["content"])
                outcome = "context.compacted" if repaired else "context.compaction_failed"
                self.assertEqual(sum(event.type == outcome for event in records), 1)

    async def test_large_observation_can_be_compacted_before_the_next_request(self):
        registry = ToolRegistry()
        registry.register(_FIXTURE, AsyncMock(return_value=ToolResult("A" * 1_100_000)))
        model = ScriptedModel(
            _call(1, prompt_tokens=2000), ModelTurn(Message("assistant", _CHECKPOINT), "stop"),
            ModelTurn(Message("assistant", "Done."), "stop"),
        )
        result = await run_agent(
            AgentSpec("fixture/model", tool_names=(_FIXTURE.name,)), "Complete the task.",
            model=model, tools=registry,
        )
        summary_request = model.next_turn.call_args_list[1].kwargs["messages"]
        self.assertIn("Middle omitted for checkpoint input", summary_request[-1].content)
        self.assertLess(estimate_messages(summary_request), INPUT_TOKEN_LIMIT)
        self.assertEqual(result.text, "Done.")
        self.assertTrue(model.next_turn.call_args.kwargs["messages"][-1].content.endswith(_CHECKPOINT))

    async def test_oversized_task_reports_budget_before_calling_the_model(self):
        model = ScriptedModel()
        with self.assertRaisesRegex(AgentRunError, "exceeds the 360000-token input budget"):
            await run_agent(AgentSpec("fixture/model"), "A" * 1_100_000, model=model)
        model.next_turn.assert_not_awaited()

    async def test_short_interactive_history_stays_verbatim(self):
        session = Session([Message("user", "Use metric units."), Message("assistant", "Understood.")])
        model = ScriptedModel(ModelTurn(Message("assistant", "Done."), "stop"))
        await run_agent(
            AgentSpec("fixture/model", context_policy=CLI_CONTEXT_POLICY),
            "Show the next value.", model=model, session=session,
        )
        model.next_turn.assert_awaited_once()
        self.assertEqual(model.next_turn.call_args.kwargs["messages"][1:3], tuple(session.messages[:2]))
        self.assertIsNone(session.checkpoint)

    async def test_a_later_task_keeps_its_own_request_during_compaction(self):
        task = "Revise the current presentation."
        first = Message("user", "Check yesterday's totals.")
        session = Session([first, Message("assistant", "Checked yesterday's totals.")])
        registry = ToolRegistry()
        registry.register(_FIXTURE, AsyncMock(return_value=ToolResult("Observed.")))
        model = ScriptedModel(
            _call(1, prompt_tokens=INPUT_TOKEN_LIMIT),
            ModelTurn(Message("assistant", _CHECKPOINT), "stop"),
            ModelTurn(Message("assistant", "Revised."), "stop"),
        )
        await run_agent(
            AgentSpec("fixture/model", tool_names=(_FIXTURE.name,)),
            task, model=model, tools=registry, session=session,
        )
        summary = json.loads(model.next_turn.call_args_list[1].kwargs["messages"][-1].content)
        self.assertEqual(summary["task"], task)
        self.assertEqual(summary["completed_history"][0]["content"], first.content)
        continued = model.next_turn.call_args.kwargs["messages"]
        self.assertIn(Message("user", task), continued)
        self.assertNotIn(first, continued)
        self.assertEqual(continued[-1].tool_call_id, "call-1")

    async def test_optional_checkpoint_failure_preserves_completed_result(self):
        observation = "Saved intermediate data.\n" + "A" * 100_000
        for error, reason in ((RuntimeError("Unavailable."), "checkpoint_model_error"),
                              (asyncio.CancelledError(), "cancelled")):
            with self.subTest(reason=reason):
                registry = ToolRegistry()
                registry.register(_FIXTURE, AsyncMock(return_value=ToolResult(observation)))
                records = []
                session = Session()
                model = ScriptedModel(_call(1), ModelTurn(Message("assistant", "Delivered."), "stop"), error)
                result = await run_agent(
                    AgentSpec("fixture/model", tool_names=(_FIXTURE.name,), context_policy=CLI_CONTEXT_POLICY),
                    "Process the data.", model=model, tools=registry, session=session, events=RunEvents(records.append),
                )
                self.assertEqual(result.text, "Delivered.")
                self.assertIsNone(session.checkpoint)
                self.assertEqual(session.messages[-2].content, observation)
                self.assertEqual(records[-1].type, "run.completed")
                failed = next(event.data for event in records if event.type == "context.compaction_failed")
                self.assertEqual((failed["phase"], failed["reason"]), ("post_turn", reason))
