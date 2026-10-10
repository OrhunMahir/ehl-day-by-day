"""What the model client sends: thinking switched off, a token limit, a reasoning level."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
        args = sent(llm.Model(provider="qwen", thinking=False, reasoning_effort="xhigh"))
        self.assertEqual(args["extra_body"]["chat_template_kwargs"]["enable_thinking"], False)
        self.assertNotIn("reasoning_effort", args)

    def test_existing_extra_body_is_kept(self):
        args = sent(llm.Model(provider="qwen", thinking=False), extra_body={"top_k": 20})
        self.assertEqual(args["extra_body"]["top_k"], 20)
        self.assertFalse(args["extra_body"]["chat_template_kwargs"]["enable_thinking"])

    def test_token_limit_and_reasoning_level(self):
        args = sent(llm.Model(provider="qwen", reasoning_effort="low", max_tokens=4000))
        self.assertEqual((args["max_tokens"], args["reasoning_effort"]), (4000, "low"))
        self.assertNotIn("extra_body", args)

    def test_a_call_may_set_its_own_token_limit(self):
        args = sent(llm.Model(provider="qwen", max_tokens=4000), max_tokens=500)
        self.assertEqual(args["max_tokens"], 500)


if __name__ == "__main__":
    unittest.main()
