"""The model client's thinking switch, and when a "no bid" is final. Invented values only."""
import asyncio
import dataclasses
import os
import sys
import unittest
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_engine_base import core_facts, material  # noqa: E402

import agent  # noqa: E402
import engine  # noqa: E402
import llm  # noqa: E402


class FakeCompletions:
    def __init__(self):
        self.sent = []

    async def create(self, **args):
        self.sent.append(args)
        message = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def sent(model: llm.Model, **kwargs) -> dict:
    fake = FakeCompletions()
    model.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    asyncio.run(model.chat([{"role": "user", "content": "hi"}], **kwargs))
    return fake.sent[0]


@patch.dict(os.environ, {"QWEN_BASE_URL": "http://127.0.0.1:9/qwen/v1", "QWEN_API_KEY": "local"})
class Thinking(unittest.TestCase):
    def test_thinking_off_for_qwen(self):
        args = sent(llm.Model(provider="qwen", thinking=False, reasoning_effort="high"))
        self.assertIs(args["extra_body"]["chat_template_kwargs"]["enable_thinking"], False)
        self.assertNotIn("reasoning_effort", args)

    def test_existing_extra_body_is_kept(self):
        args = sent(llm.Model(provider="qwen", thinking=False), extra_body={"top_k": 20})
        self.assertEqual(args["extra_body"]["top_k"], 20)
        self.assertIs(args["extra_body"]["chat_template_kwargs"]["enable_thinking"], False)

    def test_reasoning_level(self):
        args = sent(llm.Model(provider="qwen", reasoning_effort="low"))
        self.assertEqual(args["reasoning_effort"], "low")
        self.assertNotIn("extra_body", args)

    def test_default_sends_neither(self):
        args = sent(llm.Model(provider="qwen"))
        self.assertNotIn("reasoning_effort", args)
        self.assertNotIn("extra_body", args)

    def test_call_timeout(self):
        self.assertEqual(llm.Model(provider="qwen", timeout=60).client.timeout, 60)


def guarded(collected):
    return agent.Orchestrator.without_hasty_no_bid(None, collected, engine.evaluate(collected))


class TheNoBidGuard(unittest.TestCase):
    """Position 2's group is searched, but its only matching material is missing from the facts."""

    def facts_without_b1(self, *extra):
        f = core_facts()
        kept = [m for m in f.materials if m.material_number != "M-B1"]
        return dataclasses.replace(f, materials=[*kept, *extra], searched_groups=["G-A", "G-B"])

    def test_a_searched_group_without_one_recorded_result_is_not_a_no_bid(self):
        result = guarded(self.facts_without_b1())
        self.assertEqual(result.answer["decision"], "bid")
        self.assertIn("2", result.omitted)
        self.assertEqual([p["position"] for p in result.answer["positions"]], ["1"])

    def test_recorded_results_that_do_not_match_are_a_no_bid(self):
        look_alike = material("M-B9", 63, 400, 250, "Schwenkauge", "EN 9999", catalogue_group="G-B")
        result = guarded(self.facts_without_b1(look_alike))
        self.assertEqual(result.answer["decision"], "no_bid")

    def test_a_group_not_marked_as_searched_is_not_a_no_bid(self):
        look_alike = material("M-B9", 63, 400, 250, "Schwenkauge", "EN 9999", catalogue_group="G-B")
        f = dataclasses.replace(self.facts_without_b1(look_alike), searched_groups=["G-A"])
        self.assertEqual(guarded(f).answer["decision"], "bid")

    def test_bid_is_unchanged(self):
        self.assertEqual(guarded(core_facts()).answer, engine.evaluate(core_facts()).answer)
        self.assertEqual(guarded(core_facts()).answer["total_net"], float(D("3807.36")))


if __name__ == "__main__":
    unittest.main()
