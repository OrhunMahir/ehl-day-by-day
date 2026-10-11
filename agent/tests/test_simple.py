"""The board concept (simple.py, board.py): one agent per website, connected only by a shared board of typed facts.
Invented values only; no browser, no network, no model."""
import asyncio
import json
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402
import simple  # noqa: E402
from board import Board  # noqa: E402
from browser import Tab  # noqa: E402
from browsing import BrowsingAgent  # noqa: E402

SITES = ("site-a", "site-b", "site-c", "site-d")
TASK = {"access": [{"siteId": s, "url": f"https://{s}.example.test", "credentials": {}} for s in SITES],
        "brief": "Tender T-1, scenario date 2030-01-01.", "answer_schema": {},
        "submission": {"kind": "site", "siteId": "site-a"}}
SOURCE = {"site": "x", "document": "d", "date": "2029-12-01"}
START = {"tender_reference": "T-1", "scenario_date": "2030-01-01"}
POSITIONS = {"positions": [{"position": "1", "quantity": 4, "requirement": "R1", "source": SOURCE},
                           {"position": "2", "quantity": 2, "requirement": "R2", "source": SOURCE}]}
SPEC = {"revisions": [{"revision": "A", "current": True, "requirements": [
    {"requirement": "R1", "bore_mm": 50, "stroke_mm": 200, "pressure_bar": 160, "mounting": "flange",
     "catalogue_group": "G1"},
    {"requirement": "R2", "bore_mm": 80, "stroke_mm": 300, "pressure_bar": 160, "mounting": "flange",
     "catalogue_group": "G2"}], "source": SOURCE}]}
ERP = {"materials": [{"material_number": "M1", "catalogue_group": "G1", "bore_mm": 50, "stroke_mm": 200,
                      "pressure_bar": 160, "mounting": "flange", "active": True, "stock": 10, "stock_lead_weeks": 1,
                      "production_lead_weeks": 5, "conditions": [{"valid_from": "2029-01-01",
                                                                  "valid_to": "2030-12-31", "currency": "EUR",
                                                                  "price_unit": 1, "scales": [{"min_quantity": 1,
                                                                                               "price": 12.5}]}],
                      "source": SOURCE}],
       "searched_groups": ["G1"]}


def call(n, name, arguments):
    return NS(id=f"c{n}", type="function", function=NS(name=name, arguments=json.dumps(arguments)))


class FakePage:
    def __init__(self, url):
        self.url = url
        self.context = self

    def on(self, *args):
        pass

    async def title(self):
        return "page"


def tab(site):
    t = Tab(site, FakePage(f"https://{site}.example.test/"))

    async def observe(offset=0):
        return f"[{site}] a page"
    t.observe = observe
    return t


class TheBoard(unittest.IsolatedAsyncioTestCase):
    async def test_an_agent_sees_what_the_others_recorded_and_what_is_still_needed(self):
        board = Board(log=lambda *a: None)
        board.record("site-a", {**START, **POSITIONS})
        board.record("site-b", SPEC)
        news = board.news("site-c", 0)
        self.assertIn("recorded by site-a", news)
        self.assertIn('"R1"', news)
        self.assertIn("search Kataloggruppe G1", news)  # the engine asks for what the ERP can answer
        self.assertIn("bore 50", news)
        self.assertNotIn("recorded by site-b", board.news("site-b", 0))  # its own records are not repeated

    async def test_only_the_agent_that_searched_records_a_group_as_searched(self):
        # a sandbox run: the specification's agent listed every group as searched, nobody had searched any,
        # and the engine took that for a search without a match: "no bid"
        board = Board(log=lambda *a: None)
        board.record("brief", START)
        reply = board.record("site-b", {**SPEC, "searched_groups": ["G1", "G2"]})
        self.assertIn("searched_groups was not taken", reply)
        self.assertNotIn("searched_groups", board.builder.data)
        self.assertIn("revisions", board.builder.data)  # the rest of the update is kept
        board.record("site-c", ERP)  # results and the group they were searched by
        board.record("site-c", {"searched_groups": ["G2"]})  # a later search of the same agent, found nothing
        self.assertEqual(board.builder.data["searched_groups"], ["G1", "G2"])

    async def test_a_price_recorded_as_written_on_a_german_page_keeps_its_value(self):
        # a sandbox run: "186.615,00" per 100 pieces turned into 186.615 when the model made it a number itself
        board = Board(log=lambda *a: None)
        board.record("site-a", {**START, **POSITIONS})
        board.record("site-b", SPEC)
        material = {**ERP["materials"][0], "conditions": [{**ERP["materials"][0]["conditions"][0], "price_unit": "100",
                                                          "scales": [{"min_quantity": "1", "price": "186.615,00"}]}]}
        board.record("site-c", {"materials": [material], "searched_groups": ["G1"]})
        from engine import evaluate
        position = next(p for p in evaluate(board.builder.facts()).answer["positions"] if p["position"] == "1")
        self.assertEqual(position["base_unit_price"], 1866.15)

    async def test_a_bad_record_is_refused_and_changes_nothing(self):
        board = Board(log=lambda *a: None)
        with self.assertRaises(ValueError):
            board.record("site-c", {"materials": [{"material_number": "M1", "stock": "a lot"}]})
        self.assertEqual(board.version, 0)

    async def test_a_waiting_agent_is_woken_by_a_new_open_item_and_not_by_every_record(self):
        board = Board(log=lambda *a: None)
        board.record("site-a", {**START, **POSITIONS})
        seen = board.version
        waiting = asyncio.ensure_future(board.wait_for_work(seen, 2))
        await asyncio.sleep(0.05)
        board.record("site-d", {"bidder_domain": "example.test"})  # nothing new is asked for by this
        await asyncio.sleep(0.2)
        self.assertFalse(waiting.done())
        self.assertEqual(board.work_version, seen)
        board.record("site-b", SPEC)  # the specification's groups: the ERP search is asked for now
        self.assertTrue(await asyncio.wait_for(waiting, 1))
        self.assertEqual(board.work_version, board.version)
        self.assertFalse(await board.wait_for_work(board.version, 0.1))  # and nothing since

    async def test_wait_returns_as_soon_as_another_agent_records(self):
        board = Board(log=lambda *a: None)

        async def later():
            await asyncio.sleep(0.2)
            board.record("site-a", START)
        asyncio.ensure_future(later())
        started = time.time()
        self.assertTrue(await board.wait(0, timeout=5))
        self.assertLess(time.time() - started, 1)
        self.assertFalse(await board.wait(board.version, timeout=0.1))


class TwoWebsitesNoHandOff(unittest.TestCase):
    def test_the_erp_agent_starts_the_moment_the_specification_is_on_the_board(self):
        Tab.homes.clear()
        board = Board(log=lambda *a: None)
        board.record("brief", START)
        seen_by_erp, done = [], []

        class Spec:  # reads a little, then records the positions and the specification
            def __init__(self):
                self.n = 0

            async def chat(self, messages, tools=None, **kwargs):
                self.n += 1
                if self.n == 1:
                    await asyncio.sleep(0.3)
                    return NS(content="", tool_calls=[call(1, "record", {"facts": {**POSITIONS, **SPEC}})])
                return NS(content="", tool_calls=[call(self.n, "wait", {})])

        class Erp:  # waits until the board names a group, then searches it and records the material
            def __init__(self):
                self.n = 0

            async def chat(self, messages, tools=None, **kwargs):
                self.n += 1
                names = {t["function"]["name"] for t in tools}
                assert "wait" in names and "record" in names and "note" not in names and "finish" not in names
                text = "\n".join(m["content"] for m in messages if m["role"] in ("user", "tool"))
                if "search Kataloggruppe G1" in text and not done:
                    seen_by_erp.append(time.time())
                    done.append(1)
                    return NS(content="", tool_calls=[call(self.n, "record", {"facts": ERP})])
                return NS(content="", tool_calls=[call(self.n, "wait", {})])

        async def scenario():
            deadline = time.time() + 20  # few steps end it long before
            agents = [BrowsingAgent(Spec(), {"site-b": tab("site-b")}, {}, "job", "context", name="site-b",
                                    deadline=deadline, max_steps=3, log=lambda *a: None, board=board),
                      BrowsingAgent(Erp(), {"site-c": tab("site-c")}, {}, "job", "context", name="site-c",
                                    deadline=deadline, max_steps=4, log=lambda *a: None, board=board)]
            started = time.time()
            await asyncio.gather(*(a.run() for a in agents))
            return started

        import browsing
        with patch.object(browsing, "WAIT_SECONDS", 0.5):
            started = asyncio.run(scenario())
        self.assertEqual(len(seen_by_erp), 1, "the ERP agent never learnt of the catalogue group")
        self.assertLess(seen_by_erp[0] - started, 1.0)  # at once, not after the other agent ended
        self.assertEqual([m["material_number"] for m in board.builder.data["materials"]], ["M1"])


class OnlyAsLongAsItHelps(unittest.TestCase):
    def test_investigations_end_as_soon_as_the_bid_is_complete(self):
        class Forever:
            async def run(self):
                await asyncio.sleep(3600)

        async def scenario():
            lead = agent.Orchestrator(TASK, None, {s: tab(s) for s in SITES}, None, time.time())
            lead.log = lambda *a: None
            lead.collect_until = time.time() + 600
            board = Board(lead.builder, log=lead.log)
            board.complete = lambda: True
            started = time.time()
            with patch.object(simple, "COMPLETE_CHECK_SECONDS", 0.1):
                await simple.run_agents(lead, [Forever(), Forever()], board)
            return time.time() - started

        self.assertLess(asyncio.run(scenario()), 2)

    def test_an_incomplete_bid_is_not_complete(self):
        board = Board(log=lambda *a: None)
        self.assertFalse(board.complete())
        board.record("x", {**START, **POSITIONS})
        self.assertFalse(board.complete())


class TheModelOfAnAgent(unittest.IsolatedAsyncioTestCase):
    """An agent starts with the hosted model and changes to the reading model when it comes to careful facts."""

    class Answers:
        def __init__(self, name, *messages):
            self.provider, self.model, self.thinking, self.reasoning_effort = name, name, None, None
            self.messages, self.asked = list(messages), 0

        async def chat(self, messages, tools=None, **kwargs):
            self.asked += 1
            return self.messages.pop(0)

        def usage(self):
            return f"{self.asked} calls"

    def test_only_records_of_careful_facts_call_for_the_reading_model(self):
        answer = lambda *calls: NS(content="", tool_calls=list(calls))  # noqa: E731
        self.assertIsNone(simple.careful_record(answer(call(1, "click", {"element": "3"}))))
        self.assertIsNone(simple.careful_record(answer(call(1, "record", {"facts": POSITIONS}))))
        self.assertIsNone(simple.careful_record(answer(call(1, "record", {"facts": SPEC}))))
        self.assertIsNone(simple.careful_record(NS(content="thinking aloud", tool_calls=None)))
        self.assertIn("materials", simple.careful_record(answer(call(1, "record", {"facts": ERP}))))
        self.assertIn("setup_fees", simple.careful_record(answer(call(1, "record", {"facts": {
            **POSITIONS, "setup_fees": [{"position": "1", "fee_eur": "50,00"}]}}))))
        self.assertIn("materials", simple.careful_record(answer(call(1, "record", ERP))))  # facts sent bare
        broken = NS(content="", tool_calls=[NS(id="c1", type="function", function=NS(name="record", arguments="{"))])
        self.assertIn("cannot be read", simple.careful_record(broken))
        from dataclasses import fields
        import facts
        self.assertLessEqual(simple.CAREFUL_FACTS, {f.name for f in fields(facts.Facts)})

    async def test_the_record_of_careful_facts_is_asked_again_from_the_reading_model_which_stays(self):
        from llm import Stepped
        step = NS(content="", tool_calls=[call(1, "click", {"element": "3"})])
        hasty = NS(content="", tool_calls=[call(2, "record", {"facts": ERP})])
        careful = NS(content="", tool_calls=[call(3, "record", {"facts": ERP})])
        later = NS(content="", tool_calls=[call(4, "wait", {})])
        hosted, reader, said = self.Answers("hosted", step, hasty), self.Answers("reader", careful, later), []
        model = Stepped(hosted, reader, simple.careful_record, name="site-c", log=said.append)
        self.assertEqual((model.provider, model.changed), ("hosted", False))
        self.assertIs(await model.chat([{"role": "user", "content": "go"}], []), step)
        self.assertIs(await model.chat([{"role": "user", "content": "go"}], []), careful)  # not the hasty one
        self.assertIs(await model.chat([{"role": "user", "content": "go"}], []), later)
        self.assertEqual((hosted.asked, reader.asked, model.provider, model.changed), (2, 2, "reader", True))
        self.assertEqual(len(said), 1)
        self.assertIn("about to record materials", said[0])

    async def test_without_staying_only_the_careful_records_come_from_the_reading_model(self):
        from llm import Stepped
        step = NS(content="", tool_calls=[call(1, "click", {"element": "3"})])
        hasty = NS(content="", tool_calls=[call(2, "record", {"facts": ERP})])
        careful = NS(content="", tool_calls=[call(3, "record", {"facts": ERP})])
        onward = NS(content="", tool_calls=[call(4, "goto", {"url": "/next"})])
        plain = NS(content="", tool_calls=[call(5, "record", {"facts": POSITIONS})])
        hosted = self.Answers("hosted", step, hasty, onward, plain)
        reader, said = self.Answers("reader", careful), []
        model = Stepped(hosted, reader, simple.careful_record, name="site-c", log=said.append, stays=False)
        answers = [await model.chat([{"role": "user", "content": "go"}], []) for _ in range(4)]
        self.assertEqual([a is b for a, b in zip(answers, (step, careful, onward, plain))], [True] * 4)
        self.assertEqual((hosted.asked, reader.asked, model.provider, model.changed), (4, 1, "hosted", False))
        self.assertEqual(len(said), 1)

    async def test_a_step_the_hosted_model_does_not_answer_in_time_goes_to_the_reading_model(self):
        from llm import Stepped

        class Hangs(self.Answers):
            async def chat(self, messages, tools=None, **kwargs):
                self.asked += 1
                if self.asked == 1:
                    await asyncio.sleep(5)
                return self.messages.pop(0)
        late = NS(content="", tool_calls=[call(1, "click", {"element": "1"})])
        instead = NS(content="", tool_calls=[call(2, "click", {"element": "2"})])
        next_one = NS(content="", tool_calls=[call(3, "wait", {})])
        hosted, reader, said = Hangs("hosted", late, next_one), self.Answers("reader", instead), []
        model = Stepped(hosted, reader, simple.careful_record, name="site-a", log=said.append, stays=False,
                        first_seconds=0.2)
        started = time.time()
        self.assertIs(await model.chat([{"role": "user", "content": "go"}], []), instead)
        self.assertLess(time.time() - started, 2)
        hosted.messages = [next_one]
        self.assertIs(await model.chat([{"role": "user", "content": "go"}], []), next_one)  # the hosted model again
        self.assertIn("no answer from hosted", said[0])

    def test_every_agent_changes_on_its_own(self):
        lead = NS(task=TASK, tabs={site: NS(page=NS(context=NS(browser=None))) for site in SITES}, credentials={},
                  context=lambda: "context", collect_until=time.time() + 60, log=lambda *a: None)
        hosted, reader = self.Answers("hosted"), self.Answers("reader")
        agents = simple.site_agents(lead, Board(log=lambda *a: None), reader, "T-1", hosted)
        self.assertEqual([type(a.model).__name__ for a in agents], ["Stepped"] * 4)
        agents[2].model.changed = True
        self.assertEqual([a.model.provider for a in agents], ["hosted", "hosted", "reader", "hosted"])
        same = simple.site_agents(lead, Board(log=lambda *a: None), reader, "T-1")
        self.assertTrue(all(a.model is reader for a in same))


class OneAgentPerWebsite(unittest.TestCase):
    def test_every_website_gets_one_agent_with_only_its_own_tab(self):
        lead = agent.Orchestrator(TASK, None, {s: tab(s) for s in SITES}, None, time.time())
        lead.log = lambda *a: None
        agents = simple.site_agents(lead, Board(lead.builder, log=lead.log), None, "T-1")
        self.assertEqual([a.name for a in agents], list(SITES))
        self.assertTrue(all(list(a.tabs) == [a.name] for a in agents))
        self.assertTrue(all(a.board is not None for a in agents))
        self.assertTrue(all(set(a.record_schema["properties"]) >= {"positions", "materials", "discount_approvals"}
                            for a in agents))  # every kind of fact, wherever a website keeps it

    def test_the_facts_on_the_board_are_priced_and_submitted(self):
        class Signs:
            entered = None

            def __init__(self, model, tabs, credentials, task, context, deadline, log):
                Signs.entered = task

            async def run(self):
                return "receipt"

        async def scenario():
            lead = agent.Orchestrator(TASK, None, {s: tab(s) for s in SITES}, None, time.time())
            lead.log = lambda *a: None
            board = Board(lead.builder, log=lead.log)
            for update in (START, POSITIONS, SPEC, ERP):
                board.record("x", update)
            with patch.object(agent, "SubmissionAgent", Signs):
                await simple.submit(lead)
            return lead

        lead = asyncio.run(scenario())
        self.assertTrue(lead.accepted)  # a local run takes one attempt as the end
        positions = {p["position"]: p for p in lead.submitted["positions"]}
        self.assertEqual((positions["1"]["material_number"], positions["1"]["unit_price"]), ("M1", 12.5))
        self.assertEqual(positions["2"]["material_number"], "")  # G2 not searched: chosen in the form
        self.assertIn("MATERIALS", Signs.entered)


if __name__ == "__main__":
    unittest.main()
