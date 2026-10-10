"""Offline request and queue regressions; all credentials and messages are invented."""
import asyncio
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from openai import APIStatusError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm  # noqa: E402


MESSAGES = [{"role": "user", "content": "Return an invented example."}]
PROXY_URL = "https://proxy.example.invalid/qwen/v1"


def reply():
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="invented reply", tool_calls=[]))],
        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3),
    )


class FakeAsyncOpenAI:
    """Match the client surface without constructing a network client."""

    instances = []

    def __init__(self, **config):
        self.config = config
        self.requests = []
        self.handler = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.instances.append(self)

    async def create(self, **request):
        self.requests.append(request)
        if self.handler is not None:
            return await self.handler(request)
        return reply()


class HeldCalls:
    """Keep calls running so concurrency and cancellation can be observed."""

    def __init__(self):
        self.gate = asyncio.Event()
        self.entered = 0
        self.active = 0
        self.maximum = 0
        self.thresholds = {}

    def reached(self, count):
        event = self.thresholds.setdefault(count, asyncio.Event())
        if self.entered >= count:
            event.set()
        return event

    async def __call__(self, request):
        self.entered += 1
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        for count, event in self.thresholds.items():
            if self.entered >= count:
                event.set()
        try:
            await self.gate.wait()
            return reply()
        finally:
            self.active -= 1


class ModelRuntime(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeAsyncOpenAI.instances = []
        client_patch = patch.object(llm, "AsyncOpenAI", FakeAsyncOpenAI)
        environment_patch = patch.dict("os.environ", {
            "AGENT_PROVIDER": "qwen",
            "QWEN_BASE_URL": PROXY_URL,
            "QWEN_API_KEY": "invented-test-key",
        }, clear=True)
        client_patch.start()
        environment_patch.start()
        self.addCleanup(client_patch.stop)
        self.addCleanup(environment_patch.stop)

    async def cancel_tasks(self, tasks):
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def test_four_call_limit_is_shared_by_different_models(self):
        models = [llm.Model(model=f"invented-model-{number}") for number in range(3)]
        held = HeldCalls()
        for model in models:
            model.client.handler = held
        first_four = held.reached(4)
        tasks = [asyncio.create_task(models[number % 3].chat(MESSAGES)) for number in range(9)]
        self.addAsyncCleanup(self.cancel_tasks, tasks)

        await asyncio.wait_for(first_four.wait(), timeout=1)
        self.assertEqual(held.entered, 4)
        self.assertEqual(held.active, 4)
        self.assertEqual(sum(len(model.client.requests) for model in models), 4)
        held.gate.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)

        self.assertEqual(held.entered, 9)
        self.assertEqual(held.maximum, 4)
        self.assertEqual(held.active, 0)
        self.assertEqual(sum(model.calls for model in models), 9)
        self.assertEqual(sum(model.prompt_tokens for model in models), 18)
        self.assertEqual(sum(model.completion_tokens for model in models), 27)

    async def test_cancelled_queued_call_does_not_leak_a_slot_or_send_a_request(self):
        running_model, queued_model = llm.Model(), llm.Model()
        held = HeldCalls()
        running_model.client.handler = held
        queued_model.client.handler = held
        tasks = [asyncio.create_task(running_model.chat(MESSAGES)) for _ in range(4)]
        self.addAsyncCleanup(self.cancel_tasks, tasks)
        await asyncio.wait_for(held.reached(4).wait(), timeout=1)

        queued_started = asyncio.Event()

        async def queued_call():
            queued_started.set()
            return await queued_model.chat(MESSAGES)

        queued = asyncio.create_task(queued_call())
        tasks.append(queued)
        await asyncio.wait_for(queued_started.wait(), timeout=1)
        self.assertFalse(queued.done())
        self.assertEqual(queued_model.client.requests, [])
        queued.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await queued
        self.assertEqual(queued_model.client.requests, [])

        held.gate.set()
        await asyncio.wait_for(asyncio.gather(*tasks[:4]), timeout=1)
        held.gate = asyncio.Event()
        second_batch = [asyncio.create_task(queued_model.chat(MESSAGES)) for _ in range(4)]
        tasks.extend(second_batch)
        await asyncio.wait_for(held.reached(8).wait(), timeout=1)
        self.assertEqual(held.active, 4)
        held.gate.set()
        await asyncio.wait_for(asyncio.gather(*second_batch), timeout=1)
        self.assertEqual(held.maximum, 4)
        self.assertEqual(queued_model.calls, 4)

    async def test_cancelled_active_call_releases_its_slot(self):
        model = llm.Model()
        held = HeldCalls()
        model.client.handler = held
        tasks = [asyncio.create_task(model.chat(MESSAGES)) for _ in range(4)]
        self.addAsyncCleanup(self.cancel_tasks, tasks)
        await asyncio.wait_for(held.reached(4).wait(), timeout=1)
        tasks[0].cancel()
        with self.assertRaises(asyncio.CancelledError):
            await tasks[0]

        replacement = asyncio.create_task(model.chat(MESSAGES))
        tasks.append(replacement)
        await asyncio.wait_for(held.reached(5).wait(), timeout=1)
        self.assertEqual(held.active, 4)
        self.assertEqual(held.maximum, 4)
        held.gate.set()
        await asyncio.wait_for(asyncio.gather(*tasks[1:]), timeout=1)
        self.assertEqual(model.calls, 4)

    async def test_qwen_thinking_false_merges_extra_body_without_mutating_input(self):
        model = llm.Model(reasoning_effort="xhigh", thinking=False)
        extra_body = {"invented_flag": "keep", "chat_template_kwargs": {
            "invented_option": 7, "enable_thinking": True,
        }}
        original = copy.deepcopy(extra_body)
        await model.chat(MESSAGES, extra_body=extra_body)
        request = model.client.requests[0]

        self.assertEqual(request["extra_body"], {
            "invented_flag": "keep",
            "chat_template_kwargs": {"invented_option": 7, "enable_thinking": False},
        })
        self.assertEqual(extra_body, original)
        self.assertNotIn("reasoning_effort", request)

    async def test_xhigh_is_forwarded_and_a_per_call_effort_can_override_it(self):
        model = llm.Model(reasoning_effort="xhigh")
        await model.chat(MESSAGES)
        await model.chat(MESSAGES, reasoning_effort="medium")
        self.assertEqual(model.client.requests[0]["reasoning_effort"], "xhigh")
        self.assertEqual(model.client.requests[1]["reasoning_effort"], "medium")

    async def test_max_tokens_default_and_explicit_override_are_forwarded(self):
        model = llm.Model(max_tokens=500)
        await model.chat(MESSAGES)
        await model.chat(MESSAGES, max_tokens=123)
        self.assertEqual(model.client.requests[0]["max_tokens"], 500)
        self.assertEqual(model.client.requests[1]["max_tokens"], 123)
        unset = llm.Model()
        await unset.chat(MESSAGES)
        self.assertNotIn("max_tokens", unset.client.requests[0])

    async def test_permanent_request_errors_are_not_retried(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                model = llm.Model()
                error = APIStatusError(
                    "invented permanent error",
                    response=httpx.Response(status, request=httpx.Request("POST", PROXY_URL)),
                    body={"error": "invented permanent error"},
                )

                async def fail(request):
                    raise error

                model.client.handler = fail
                with patch.object(llm.asyncio, "sleep", new_callable=AsyncMock) as sleep:
                    with self.assertRaises(APIStatusError) as caught:
                        await model.chat(MESSAGES)
                    sleep.assert_not_awaited()
                self.assertIs(caught.exception, error)
                self.assertEqual(len(model.client.requests), 1)
                self.assertEqual(model.calls, 0)

    async def test_client_uses_runtime_credentials_and_disables_sdk_retries(self):
        model = llm.Model(model="invented-model")
        message = await model.chat(MESSAGES)
        self.assertEqual(model.client.config, {
            "base_url": PROXY_URL, "api_key": "invented-test-key", "timeout": 240, "max_retries": 0,
        })
        self.assertEqual(model.client.requests[0]["model"], "invented-model")
        self.assertGreaterEqual(message.elapsed, 0)


if __name__ == "__main__":
    unittest.main()
