"""The kit's plumbing against fakes: reading the run, submitting the answer, the local scouting mode, the browsers'
CDP URLs, one call to each hosted model, and an entry point that exits cleanly without an answer."""
import io
import json
import os
import sys
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent  # noqa: E402
import browsers  # noqa: E402
import llm  # noqa: E402
import models  # noqa: E402
import run as contract  # noqa: E402
import simple  # noqa: E402

RUN = {
    "run_id": "run_test", "brief": "Prepare the bid.", "answer_schema": {"type": "object"},
    "access": [{"siteId": "tender-portal", "url": "https://tender.example/",
                "credentials": {"username": "u", "password": "s3cret"}}],
    "submission": {"kind": "api", "url": "http://placeholder/v1/runs/current/answer"},
}


class FakeServer:
    """One HTTP server for the API and the models: answers each request with the next scripted reply per path."""

    def __init__(self, replies: dict[str, list[tuple[int, dict]]]):
        self.replies, self.requests = replies, []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_one(self):
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                # self.headers matches header names in any case: urllib sends X-api-key
                fake.requests.append({"method": self.command, "path": self.path, "headers": self.headers,
                                      "body": body})
                status, reply = fake.replies[self.path].pop(0)
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = handle_one

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Case(unittest.IsolatedAsyncioTestCase):
    def serve(self, replies):
        server = FakeServer(replies)
        self.addCleanup(server.close)
        return server


class ReadingTheRun(Case):
    def test_a_run_reads_its_task_with_the_run_token(self):
        api = self.serve({"/v1/runs/current": [(200, RUN)]})
        run, token = contract.load_run({"CHALLENGE_API_URL": api.url + "/", "CHALLENGE_RUN_TOKEN": "rt_secret"})
        self.assertEqual((run, token), (RUN, "rt_secret"))
        [request] = api.requests
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["headers"]["x-api-key"], "rt_secret")
        # the API's edge refuses Python's default urllib agent
        self.assertEqual(request["headers"]["User-Agent"], contract.USER_AGENT)

    def test_a_restarting_api_is_asked_again(self):
        api = self.serve({"/v1/runs/current": [(503, {"error": "restarting"}), (200, RUN)]})
        with patch("time.sleep"):
            run, _ = contract.load_run({"CHALLENGE_API_URL": api.url, "CHALLENGE_RUN_TOKEN": "rt_secret"})
        self.assertEqual(run, RUN)
        self.assertEqual(len(api.requests), 2)

    def test_a_refused_token_ends_the_agent_at_once(self):
        api = self.serve({"/v1/runs/current": [(401, {"error": "unknown token"})]})
        with self.assertRaises(SystemExit) as stop:
            contract.load_run({"CHALLENGE_API_URL": api.url, "CHALLENGE_RUN_TOKEN": "rt_wrong"})
        self.assertIn("HTTP 401", str(stop.exception))
        self.assertEqual(len(api.requests), 1)

    def test_locally_it_reads_the_scouting_run_that_atira_scout_env_exports(self):
        run, token = contract.load_run({"CHALLENGE_SCOUTING": json.dumps(RUN)})
        self.assertEqual((run, token), (RUN, None))

    def test_without_a_run_or_the_scouting_logins_it_says_how_to_load_them(self):
        with self.assertRaises(SystemExit) as stop:
            contract.load_run({})
        self.assertIn("atira scout --env", str(stop.exception))
        with self.assertRaises(SystemExit) as stop:
            contract.load_run({"CHALLENGE_SCOUTING": "not json"})
        self.assertIn("atira scout --env", str(stop.exception))


    def test_seconds_left_counts_down_to_the_runs_expiry(self):
        run = {**RUN, "expires_at": 1_700_000_600_000}
        self.assertEqual(contract.seconds_left(run, now=1_700_000_000), 600)
        self.assertEqual(contract.seconds_left({**RUN, "expires_at": None}), float("inf"))

class SubmittingTheAnswer(Case):
    def run_on(self, api):
        return {**RUN, "submission": {"kind": "api", "url": api.url + "/v1/runs/current/answer"}}

    def test_the_answer_is_posted_as_json_with_the_run_token(self):
        api = self.serve({"/v1/runs/current/answer": [(400, {"issues": ["total_net is missing"]}),
                                                      (202, {"status": "accepted"})]})
        run = self.run_on(api)
        self.assertEqual(contract.submit_answer(run, "rt_secret", {"bid": True})[0], 400)
        status, body = contract.submit_answer(run, "rt_secret", {"bid": True, "total_net": 1.5})
        self.assertEqual((status, json.loads(body)), (202, {"status": "accepted"}))
        request = api.requests[-1]
        self.assertEqual((request["method"], request["body"]), ("POST", {"bid": True, "total_net": 1.5}))
        self.assertEqual(request["headers"]["x-api-key"], "rt_secret")

    def test_a_site_submission_or_a_local_run_is_not_posted(self):
        with self.assertRaises(ValueError):
            contract.submit_answer({**RUN, "submission": {"kind": "site", "url": "https://tender.example/"}},
                                   "rt_secret", {})
        with self.assertRaises(ValueError):
            contract.submit_answer(RUN, None, {})


class TheBrowsers(unittest.TestCase):
    def test_cdp_urls_lists_every_browser_of_the_run(self):
        both = ["http://127.0.0.1:9222", "http://127.0.0.1:9223"]
        self.assertEqual(browsers.cdp_urls({"CDP_URLS": json.dumps(both), "CDP_URL": both[0]}), both)
        self.assertEqual(browsers.cdp_urls({"CDP_URL": both[0]}), both[:1])
        self.assertEqual(browsers.cdp_urls({"CDP_URLS": "broken", "CDP_URL": both[0]}), both[:1])
        self.assertEqual(browsers.cdp_urls({}), [])


class TheModels(Case):
    async def test_one_call_to_qwen(self):
        reply = {"id": "c1", "object": "chat.completion", "created": 0, "model": models.QWEN_MODEL, "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "Weiter"}}]}
        qwen = self.serve({"/v1/chat/completions": [(200, reply)]})
        with patch.dict(os.environ, {"QWEN_BASE_URL": qwen.url + "/v1", "QWEN_API_KEY": "rt_secret"}):
            self.assertEqual(await models.ask_qwen("Which button submits?"), "Weiter")
        [request] = qwen.requests
        self.assertEqual(request["body"]["model"], models.QWEN_MODEL)
        self.assertEqual(request["headers"]["Authorization"], "Bearer rt_secret")

    async def test_one_decision_from_clef(self):
        clef = self.serve({"/v1/decide": [(200, {"answers": {"q": {"type": "noul", "noul": 0.97}}})]})
        with patch.dict(os.environ, {"CLEF_BASE_URL": clef.url + "/", "CLEF_API_KEY": "local"}):
            self.assertEqual(await models.ask_clef({"page": "Angebot eingegangen"}, "Is the bid in?"), 0.97)
        [request] = clef.requests
        self.assertEqual(request["body"]["questions"], {"q": {"type": "noul", "instructions": "Is the bid in?"}})
        self.assertEqual(request["headers"]["Authorization"], "Bearer local")


class TheProviderOfARun(Case):
    """The callers speak chat completions and pass the same parameters for every model; `llm.Model` sends what the
    provider takes."""

    REPLY = {"id": "c1", "object": "chat.completion", "created": 0, "model": "m", "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}]}
    ANSWER = {"id": "r1", "object": "response", "created_at": 0, "model": "m", "status": "completed", "output": [
        {"type": "reasoning", "id": "rs1", "summary": []},
        {"type": "message", "id": "m1", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": "ok", "annotations": []}]}],
        "usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}}
    TOOL_CALL = {**ANSWER, "output": [{"type": "function_call", "id": "f1", "call_id": "call_2", "name": "look",
                                       "arguments": "{\"tab\": 1}", "status": "completed"}]}
    NO_SUCH_EFFORT = {"error": {"message": "Unsupported value: 'reasoning.effort' does not support 'xhigh' with this "
                                           "model.", "type": "invalid_request_error", "param": "reasoning.effort"}}
    NOT_THIS_WAY = {"error": {"message": "Invalid request.", "type": "invalid_request_error", "param": None}}
    LOOK = {"type": "function", "function": {"name": "look", "description": "Read a tab.", "parameters": {
        "type": "object", "properties": {"tab": {"type": "integer"}}}}}

    def openai(self, replies: dict, effort: str | None = None) -> tuple[llm.Model, FakeServer]:
        server = self.serve(replies)
        with patch.dict(os.environ, {"OPENAI_BASE_URL": server.url + "/v1", "OPENAI_API_KEY": "sk-invented"}):
            return llm.Model(provider="openai", model="m", reasoning_effort=effort), server

    async def test_a_conversation_with_tools_goes_through_the_responses_interface(self):
        model, server = self.openai({"/v1/responses": [(200, self.TOOL_CALL)]}, effort="low")
        conversation = [
            {"role": "system", "content": "You work one website."},
            {"role": "user", "content": "Read the first tab."},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "look", "arguments": "{\"tab\": 0}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "a page"}]
        message = await model.chat(conversation, [self.LOOK], temperature=0.2,
                                   tool_choice={"type": "function", "function": {"name": "look"}})
        [call] = message.tool_calls
        self.assertEqual((message.content, call.id, call.function.name, call.function.arguments),
                         (None, "call_2", "look", "{\"tab\": 1}"))
        body = server.requests[0]["body"]
        self.assertEqual(body["input"], [
            {"role": "system", "content": "You work one website."},
            {"role": "user", "content": "Read the first tab."},
            {"type": "function_call", "call_id": "call_1", "name": "look", "arguments": "{\"tab\": 0}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "a page"}])
        self.assertEqual(body["tools"], [{"type": "function", "strict": False, **self.LOOK["function"]}])
        self.assertEqual((body["tool_choice"], body["reasoning"], body["store"]),
                         ({"type": "function", "name": "look"}, {"effort": "low"}, False))
        self.assertNotIn("temperature", body)
        self.assertEqual(model.usage(), "1 calls, 7 prompt + 3 completion tokens")

    async def test_a_limited_answer_gets_room_to_think_and_the_lowest_effort(self):
        model, server = self.openai({"/v1/responses": [(200, self.ANSWER)]}, effort="medium")
        message = await model.chat([{"role": "user", "content": "report"}], temperature=0.2, max_tokens=2000)
        body = server.requests[0]["body"]
        self.assertEqual((message.content, message.tool_calls), ("ok", None))
        self.assertEqual((body["max_output_tokens"], body["reasoning"]),
                         (2000 + llm.REASONING_HEADROOM_TOKENS, {"effort": "low"}))

    async def test_a_refused_effort_is_lowered_at_once_and_for_the_calls_after(self):
        model, server = self.openai({"/v1/responses": [(400, self.NO_SUCH_EFFORT), (200, self.ANSWER),
                                                       (200, self.ANSWER)]}, effort="xhigh")
        self.assertEqual((await model.chat([{"role": "user", "content": "one"}])).content, "ok")
        await model.chat([{"role": "user", "content": "two"}])
        self.assertEqual([request["body"]["reasoning"]["effort"] for request in server.requests],
                         ["xhigh", "high", "high"])

    async def test_a_refused_responses_request_is_asked_through_chat_completions_without_thinking(self):
        model, server = self.openai({"/v1/responses": [(400, self.NOT_THIS_WAY)],
                                     "/v1/chat/completions": [(200, self.REPLY), (200, self.REPLY)]}, effort="low")
        self.assertEqual((await model.chat([{"role": "user", "content": "one"}], [self.LOOK])).content, "ok")
        await model.chat([{"role": "user", "content": "two"}], [self.LOOK], temperature=0.2, max_tokens=500)
        self.assertEqual([request["path"] for request in server.requests],
                         ["/v1/responses", "/v1/chat/completions", "/v1/chat/completions"])
        first, second = server.requests[1]["body"], server.requests[2]["body"]
        self.assertEqual((first["reasoning_effort"], second["reasoning_effort"]), ("none", "none"))
        self.assertEqual(first["tools"], [self.LOOK])
        self.assertNotIn("temperature", second)

    async def test_the_hosted_model_is_sent_what_the_caller_passed(self):
        server = self.serve({"/v1/chat/completions": [(200, self.REPLY)]})
        with patch.dict(os.environ, {"QWEN_BASE_URL": server.url + "/v1", "QWEN_API_KEY": "rt_secret"}):
            model = llm.Model(provider="qwen", reasoning_effort="xhigh")
        await model.chat([{"role": "user", "content": "step"}], temperature=0.2, max_tokens=2000)
        [request] = server.requests
        self.assertEqual((request["body"]["temperature"], request["body"]["max_tokens"],
                          request["body"]["reasoning_effort"]), (0.2, 2000, "xhigh"))

    async def test_the_hosted_model_without_thinking_keeps_its_switch_and_its_answer_limit(self):
        server = self.serve({"/v1/chat/completions": [(200, self.REPLY)]})
        with patch.dict(os.environ, {"QWEN_BASE_URL": server.url + "/v1", "QWEN_API_KEY": "rt_secret"}):
            model = llm.Model(provider="qwen", thinking=False, max_tokens=4000, reasoning_effort="low")
        await model.chat([{"role": "user", "content": "step"}], temperature=0.2)
        body = server.requests[0]["body"]
        self.assertEqual((body["chat_template_kwargs"], body["max_tokens"]), ({"enable_thinking": False}, 4000))
        self.assertNotIn("reasoning_effort", body)

    async def test_a_key_the_provider_turns_down_hands_the_run_to_the_hosted_model(self):
        no_credit = {"error": {"message": "You exceeded your current quota.", "type": "insufficient_quota",
                               "code": "insufficient_quota"}}
        server = self.serve({"/v1/responses": [(429, no_credit)],
                             "/v1/chat/completions": [(200, self.REPLY), (200, self.REPLY)]})
        with patch.dict(os.environ, {"QWEN_BASE_URL": server.url + "/v1", "QWEN_API_KEY": "rt_secret",
                                     "OPENAI_BASE_URL": server.url + "/v1", "OPENAI_API_KEY": "sk-invented"}):
            hosted = llm.Model(provider="qwen", thinking=False)
            model = llm.Model(provider="openai", model="m", reasoning_effort="low", fallback=hosted)
        self.assertEqual((await model.chat([{"role": "user", "content": "one"}], [self.LOOK], temperature=0.2)).content, "ok")
        await model.chat([{"role": "user", "content": "two"}], [self.LOOK], temperature=0.2)
        self.assertEqual([request["path"] for request in server.requests],
                         ["/v1/responses", "/v1/chat/completions", "/v1/chat/completions"])
        self.assertEqual(server.requests[2]["body"]["model"], hosted.model)
        self.assertEqual(server.requests[2]["body"]["temperature"], 0.2)
        self.assertIn("then qwen/", model.usage())

    def test_the_websites_are_read_by_the_larger_model_only_where_its_key_is_set(self):
        with patch.dict(os.environ, {"QWEN_BASE_URL": "http://127.0.0.1:9/v1", "QWEN_API_KEY": "rt_secret"}):
            os.environ.pop("OPENAI_API_KEY", None)
            hosted = llm.Model(provider="qwen", thinking=False)
            self.assertIs(simple.reading_model(hosted), hosted)
            with patch.dict(os.environ, {"OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "OPENAI_API_KEY": "sk-invented"}):
                reader = simple.reading_model(hosted)
        self.assertEqual((reader.provider, reader.model, reader.reasoning_effort, reader.fallback),
                         (simple.READER_PROVIDER, simple.READER_MODEL, simple.READER_REASONING, hosted))
        self.assertIn("reasoning low", simple.described(reader))
        self.assertIn("without thinking", simple.described(hosted))

    async def test_outside_a_run_the_developers_platform_token_goes_in_a_header(self):
        with patch.dict(os.environ, {"ATIRA_TEAM_KEY": "token-invented"}):
            model, server = self.openai({"/v1/responses": [(200, self.ANSWER)]})
        await model.chat([{"role": "user", "content": "one"}])
        self.assertEqual(server.requests[0]["headers"]["x-atira-team-key"], "token-invented")
        self.assertEqual(server.requests[0]["headers"]["Authorization"], "Bearer sk-invented")


if __name__ == "__main__":
    unittest.main()
