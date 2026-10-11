"""The prompt cache key the model client sends: one per conversation, only through the Responses interface."""
import asyncio
import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import BadRequestError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm  # noqa: E402

INSTRUCTIONS = {"role": "system", "content": "You work one website for the whole run."}
SHOP = {"role": "user", "content": "# Your websites\n- shop\n\n# Current page\n[shop] Welcome"}
MAIL = {"role": "user", "content": "# Your websites\n- mail\n\n# Current page\n[mail] Inbox (3)"}
LATER = [{"role": "assistant", "content": "", "tool_calls": [
             {"id": "call_1", "type": "function", "function": {"name": "look", "arguments": "{}"}}]},
         {"role": "tool", "tool_call_id": "call_1", "content": "a page"},
         {"role": "user", "content": "Board update: one more open item"}]
LOOK = {"type": "function", "function": {"name": "look", "description": "Read the page.", "parameters": {
    "type": "object", "properties": {}}}}


class FakeResponses:
    """Records every request; raises the refusals it was given first, then answers."""

    def __init__(self, *refusals):
        self.sent, self.refusals = [], list(refusals)

    async def create(self, **request):
        self.sent.append(request)
        await asyncio.sleep(0)  # like a real call, it lets the other calls go on meanwhile
        if self.refusals:
            raise self.refusals.pop(0)
        text = SimpleNamespace(type="output_text", text="ok")
        return SimpleNamespace(output=[SimpleNamespace(type="message", content=[text])], usage=None)


class FakeCompletions:
    def __init__(self):
        self.sent = []

    async def create(self, **args):
        self.sent.append(args)
        message = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def refusal(message: str, param: str | None) -> BadRequestError:
    """A 400 as the SDK raises it."""
    body = {"message": message, "type": "invalid_request_error", "param": param, "code": None}
    response = httpx.Response(400, request=httpx.Request("POST", "http://127.0.0.1:9/v1/responses"))
    return BadRequestError(f"Error code: 400 - {{'error': {body}}}", response=response, body=body)


def faked(model: llm.Model, *refusals) -> tuple[FakeResponses, FakeCompletions]:
    responses, completions = FakeResponses(*refusals), FakeCompletions()
    model.client = SimpleNamespace(responses=responses, chat=SimpleNamespace(completions=completions))
    return responses, completions


def ask(model: llm.Model, *conversations: list[dict]) -> str:
    """One call per conversation, with a tool; returns what the model client printed."""
    with contextlib.redirect_stdout(io.StringIO()) as printed:
        for conversation in conversations:
            asyncio.run(model.chat(conversation, [LOOK], temperature=0.2))
    return printed.getvalue()


@patch.dict(os.environ, {"OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "OPENAI_API_KEY": "sk-invented",
                         "QWEN_BASE_URL": "http://127.0.0.1:9/qwen/v1", "QWEN_API_KEY": "local"})
class CacheKey(unittest.TestCase):
    def test_one_conversation_keeps_its_key_as_it_grows(self):
        key = llm._cache_key([INSTRUCTIONS, SHOP])
        self.assertEqual(llm._cache_key([INSTRUCTIONS, SHOP, *LATER]), key)
        self.assertLessEqual(len(key), 32)

    def test_another_first_message_gets_another_key(self):
        self.assertNotEqual(llm._cache_key([INSTRUCTIONS, SHOP]), llm._cache_key([INSTRUCTIONS, MAIL]))
        self.assertNotEqual(llm._cache_key([SHOP]), llm._cache_key([MAIL]))

    def test_every_responses_request_carries_its_conversations_key(self):
        model = llm.Model(provider="openai", model="m", reasoning_effort="low")
        responses, _ = faked(model)
        printed = ask(model, [INSTRUCTIONS, SHOP], [INSTRUCTIONS, MAIL], [INSTRUCTIONS, SHOP, *LATER])
        keys = [request["prompt_cache_key"] for request in responses.sent]
        self.assertEqual(keys, [llm._cache_key([INSTRUCTIONS, SHOP]), llm._cache_key([INSTRUCTIONS, MAIL]), keys[0]])
        self.assertNotEqual(keys[0], keys[1])
        self.assertEqual(printed.count("prompt cache key"), 1)

    def test_chat_completions_are_never_sent_a_key(self):
        hosted = llm.Model(provider="qwen", thinking=False)
        _, completions = faked(hosted)
        ask(hosted, [INSTRUCTIONS, SHOP])
        model = llm.Model(provider="openai", model="m", reasoning_effort="low")
        responses, switched = faked(model, refusal("Invalid request.", None), refusal("Invalid request.", None))
        ask(model, [INSTRUCTIONS, SHOP], [INSTRUCTIONS, SHOP, *LATER])
        # refused for another reason: asked once more without the key, refused again, then no Responses any more
        self.assertEqual(["prompt_cache_key" in request for request in responses.sent], [True, False])
        self.assertEqual((len(responses.sent), len(completions.sent), len(switched.sent)), (2, 1, 2))
        for args in completions.sent + switched.sent:
            self.assertNotIn("prompt_cache_key", args)
            self.assertNotIn("prompt_cache_key", args.get("extra_body") or {})

    def test_a_refused_key_is_left_out_and_the_call_asked_again_through_responses(self):
        for message, param in [("Unknown parameter: 'prompt_cache_key'.", "prompt_cache_key"),
                               ("Unrecognized request argument supplied: prompt_cache_key", None)]:
            with self.subTest(message):
                model = llm.Model(provider="openai", model="m", reasoning_effort="low")
                responses, completions = faked(model, refusal(message, param))
                printed = ask(model, [INSTRUCTIONS, SHOP], [INSTRUCTIONS, MAIL])
                self.assertEqual(["prompt_cache_key" in request for request in responses.sent], [True, False, False])
                self.assertEqual([request["reasoning"] for request in responses.sent], [{"effort": "low"}] * 3)
                self.assertEqual((model.responses, model.effort, completions.sent), (True, None, []))
                self.assertIn("refused a parameter", printed)

    def test_a_refusal_that_names_nothing_costs_the_key_before_the_thinking(self):
        model = llm.Model(provider="openai", model="m", reasoning_effort="low")
        responses, completions = faked(model, refusal("Invalid request.", None))
        ask(model, [INSTRUCTIONS, SHOP], [INSTRUCTIONS, MAIL])
        self.assertEqual(["prompt_cache_key" in request for request in responses.sent], [True, False, False])
        self.assertEqual([request["reasoning"] for request in responses.sent], [{"effort": "low"}] * 3)
        self.assertEqual((model.responses, model.effort, completions.sent), (True, None, []))

    def test_calls_refused_at_the_same_time_all_stay_with_responses(self):
        # the website agents share one model: a call sent with the key can be refused after another call's refusal
        model = llm.Model(provider="openai", model="m", reasoning_effort="low")
        responses, completions = faked(model, *(refusal("Unknown parameter: 'prompt_cache_key'.", "prompt_cache_key")
                                                for _ in range(2)))

        async def together():
            await asyncio.gather(model.chat([INSTRUCTIONS, SHOP], [LOOK]), model.chat([INSTRUCTIONS, MAIL], [LOOK]))
        with contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(together())
        self.assertEqual(["prompt_cache_key" in request for request in responses.sent], [True, True, False, False])
        self.assertEqual((model.responses, completions.sent), (True, []))


if __name__ == "__main__":
    unittest.main()
