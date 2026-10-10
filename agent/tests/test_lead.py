"""The lead's own rules, with invented facts: the run's schedule, how facts are added, and when a "no bid" is final."""
import asyncio
import json
import sys
import time
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent  # noqa: E402

TASK = {"access": [], "brief": "", "answer_schema": {}, "submission": {}}


def lead(task=TASK, started=None):
    orchestrator = agent.Orchestrator(task, None, {"site": None}, None, started or time.time())
    orchestrator.log = lambda text: None
    return orchestrator


def material(number, group, bore, stroke, price):
    return {"material_number": number, "active": True, "catalogue_group": group, "bore_mm": bore, "stroke_mm": stroke,
            "pressure_bar": 160, "mounting": "flange", "stock": 10, "stock_lead_weeks": 1, "production_lead_weeks": 5,
            "conditions": [{"valid_from": "2029-01-01", "valid_to": "2031-01-01",
                            "scales": [{"min_quantity": 1, "price": price}]}]}


def facts_with(searched, materials):
    return {
        "tender_reference": "T-1", "scenario_date": "2030-01-01",
        "positions": [{"position": "1", "quantity": 4, "requirement": "R1"},
                      {"position": "2", "quantity": 2, "requirement": "R2"}],
        "revisions": [{"revision": "A", "current": True, "requirements": [
            {"requirement": "R1", "bore_mm": 50, "stroke_mm": 200, "pressure_bar": 160, "mounting": "flange",
             "catalogue_group": "G1"},
            {"requirement": "R2", "bore_mm": 80, "stroke_mm": 300, "pressure_bar": 160, "mounting": "flange",
             "catalogue_group": "G2"}]}],
        "searched_groups": searched, "materials": materials,
    }


MATCH_1 = material("M1", "G1", 50, 200, 100)
NEAR_MISS_2 = material("M9", "G2", 80, 999, 70)
MATCH_2 = material("M2", "G2", 80, 300, 50)


def priced(text):
    return json.loads(text.split("\n", 1)[1] if text.startswith("facts so far") else text)


class TheSchedule(unittest.TestCase):
    def minutes(self, task, started):
        orchestrator = lead(task, started)
        now = time.time()
        return [round((moment - now) / 60, 1)
                for moment in (orchestrator.collect_until, orchestrator.submit_from, orchestrator.deadline)]

    def plan(self, minutes):  # investigations end, pricing and submission start, the end
        return [round(minutes * agent.COLLECT_SHARE, 1), round(minutes * agent.SUBMIT_SHARE, 1), float(minutes)]

    def test_a_scored_run_works_with_the_time_the_platform_gives(self):
        now = time.time()
        self.assertEqual(self.minutes({**TASK, "expires_at": (now + 1200) * 1000}, now), self.plan(20))

    def test_a_run_that_expires_later_still_plans_with_twenty_minutes(self):
        now = time.time()
        self.assertEqual(self.minutes({**TASK, "expires_at": (now + 7200) * 1000}, now), self.plan(20))
        self.assertEqual(self.minutes(TASK, now), self.plan(20))

    def test_a_run_close_to_its_end_plans_with_what_is_left(self):
        now = time.time()
        self.assertEqual(self.minutes({**TASK, "expires_at": (now + 300) * 1000}, now), self.plan(5))

    def test_the_submission_keeps_the_time_five_positions_took(self):
        self.assertGreaterEqual(20 * 60 * (1 - agent.SUBMIT_SHARE), 487 + 80)  # the slowest measured, plus a margin


class AddingFacts(unittest.TestCase):
    def test_a_later_update_keeps_the_records_added_before(self):
        orchestrator = lead()
        orchestrator.add(facts_with(["G1"], [MATCH_1, NEAR_MISS_2]))
        result = priced(orchestrator.add({"searched_groups": ["G2"], "materials": [MATCH_2]}))
        self.assertEqual(len(orchestrator.builder.data["materials"]), 3)
        self.assertEqual([p["position"] for p in result["answer"]["positions"]], ["1", "2"])

    def test_a_bad_update_is_named_and_changes_nothing(self):
        orchestrator = lead()
        orchestrator.add(facts_with(["G1"], [MATCH_1]))
        before = json.dumps(orchestrator.builder.data, sort_keys=True, default=str)
        self.assertIn("bogus", orchestrator.add({"bogus": 1}))
        self.assertEqual(json.dumps(orchestrator.builder.data, sort_keys=True, default=str), before)

    def test_the_model_cannot_set_which_sender_decides_what(self):
        orchestrator = lead()
        orchestrator.add(facts_with(["G1"], [MATCH_1]))
        orchestrator.add({"authority": {"anything": "anyone"}})
        self.assertNotIn("authority", orchestrator.builder.data)
        self.assertNotIn("authority", agent.facts_schema()["properties"])

    def test_pricing_without_facts_asks_for_them(self):
        self.assertIn("add_facts", lead().price())


class WhenANoBidIsFinal(unittest.TestCase):
    def test_a_known_candidate_of_an_unsearched_group_is_not_a_search(self):
        result = priced(lead().add(facts_with(["G1"], [MATCH_1, NEAR_MISS_2])))
        self.assertEqual(result["answer"]["decision"], "bid")
        # not a no-bid: position 2 is bid with the nearest candidate until its group is searched on purpose
        self.assertEqual([(p["position"], p["material_number"]) for p in result["answer"]["positions"]],
                         [("1", MATCH_1["material_number"]), ("2", NEAR_MISS_2["material_number"])])
        self.assertEqual(result["omitted"], [])
        self.assertIn("2", result["incomplete"])

    def test_a_group_searched_on_purpose_without_a_match_is_a_no_bid(self):
        result = priced(lead().add(facts_with(["G1", "G2"], [MATCH_1, NEAR_MISS_2])))
        self.assertEqual(result["answer"]["decision"], "no_bid")
        self.assertEqual(result["answer"]["positions"], [])

    def test_with_every_match_found_all_positions_are_bid(self):
        result = priced(lead().add(facts_with(["G1", "G2"], [MATCH_1, NEAR_MISS_2, MATCH_2])))
        self.assertEqual(result["answer"]["decision"], "bid")
        self.assertEqual(result["omitted"], [])


class FakePage:
    """Stands in for a browser page; `opened` counts the further contexts asked of its browser."""

    def __init__(self, browser=None):
        self.context = self
        self.browser = browser or self
        self.opened = 0
        self.fails = False

    async def new_context(self):
        if self.fails:
            raise RuntimeError("no further context")
        self.opened += 1
        return FakePage(self)

    async def new_page(self):
        return self

    async def goto(self, url, **kwargs):
        self.url = url


class FakeTab:
    def __init__(self, site_id, page, log=print):
        self.site_id, self.page = site_id, page


class SlowSubAgent:
    """An investigation that takes a moment and remembers which tab it worked in and when."""

    spans: list = []

    def __init__(self, model, tabs, credentials, task, context, name, deadline, log):
        self.tab = next(iter(tabs.values()))

    async def run(self):
        started = time.time()
        await asyncio.sleep(0.2)
        SlowSubAgent.spans.append((self.tab, started, time.time()))
        return "report"


class SeveralInvestigationsOfOneWebsite(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        SlowSubAgent.spans = []
        self.page = FakePage()
        task = {**TASK, "access": [{"siteId": "erp", "url": "https://erp.example.test"}]}
        self.lead = agent.Orchestrator(task, None, {"erp": FakeTab("erp", self.page)}, None, time.time())
        self.lead.log = lambda text: None

    async def ask(self, count):
        with patch.object(agent, "BrowsingAgent", SlowSubAgent), patch.object(agent, "Tab", FakeTab):
            started = time.time()
            await asyncio.gather(*(self.lead.investigate("erp", f"job {n}") for n in range(count)))
            return time.time() - started

    async def test_no_investigation_starts_with_too_little_time_left(self):
        self.lead.collect_until = time.time() + agent.MIN_INVESTIGATION_SECONDS - 5
        with patch.object(agent, "BrowsingAgent", SlowSubAgent), patch.object(agent, "Tab", FakeTab):
            reply = await self.lead.investigate("erp", "one more job")
        self.assertIn("Too little time", reply)
        self.assertEqual(SlowSubAgent.spans, [])
        self.assertEqual(self.lead.tab_count["erp"], 1)  # no further tab was opened

    async def test_they_run_side_by_side_each_in_a_tab_of_its_own(self):
        took = await self.ask(3)
        self.assertLess(took, 0.45)
        self.assertEqual(len({id(tab) for tab, _, _ in SlowSubAgent.spans}), 3)
        self.assertEqual(self.page.opened, 2)

    async def test_free_tabs_are_used_again_before_new_ones_are_opened(self):
        await self.ask(3)
        await self.ask(3)
        self.assertEqual(self.page.opened, 2)
        self.assertEqual(len(self.lead.free_tabs["erp"]), 3)

    async def test_no_more_than_the_limit_work_one_website_at_once(self):
        await self.ask(agent.TABS_PER_SITE + 2)
        self.assertEqual(self.page.opened, agent.TABS_PER_SITE - 1)
        moments = sorted(moment for _, start, end in SlowSubAgent.spans for moment in (start + 0.01, end - 0.01))
        busiest = max(sum(start <= moment <= end for _, start, end in SlowSubAgent.spans) for moment in moments)
        self.assertEqual(busiest, agent.TABS_PER_SITE)

    async def test_without_a_further_tab_they_take_turns_on_the_one_there_is(self):
        self.page.fails = True
        took = await self.ask(3)
        self.assertGreater(took, 0.55)
        self.assertEqual(len({id(tab) for tab, _, _ in SlowSubAgent.spans}), 1)


def tool_call(name, arguments):
    from types import SimpleNamespace as NS
    return NS(content="", tool_calls=[NS(id="c", function=NS(name=name, arguments=json.dumps(arguments)))])


class StandInModel:
    """The lead asks for one round of investigations and then waits; the recorder turns a report into facts.
    `recorder` decides what the recorder's calls return, one entry per call."""

    def __init__(self, recorder, recorder_delay=0.0):
        self.recorder, self.recorder_delay = list(recorder), recorder_delay
        self.lead_calls, self.states = [], []

    async def chat(self, messages, tools, **kwargs):
        forced = kwargs.get("tool_choice", {}).get("function", {}).get("name")
        if len(tools) == 1:  # the recorder has only add_facts
            await asyncio.sleep(self.recorder_delay)
            update = self.recorder.pop(0) if self.recorder else {}
            if isinstance(update, Exception):
                raise update
            return tool_call("add_facts", {"facts": update})
        self.lead_calls.append(forced or "free")
        self.states += [m["content"] for m in messages if m["role"] == "user" and "State of the bid" in m["content"]]
        if forced == "add_facts":
            return tool_call("add_facts", {"facts": facts_with(["G1"], [MATCH_1])})
        if forced == "submit_bid":
            return tool_call("submit_bid", {"hints": ""})
        if len(self.lead_calls) == 1:
            return tool_call("investigate", {"site": "erp", "task": "read"})
        await asyncio.sleep(0.05)
        return tool_call("calculate", {"expression": "1"})


class QuickSubAgent:
    def __init__(self, *args, **kwargs):
        pass

    async def run(self):
        return "report"

    def _report(self, text):
        return text


class Signs:
    def __init__(self, *args, **kwargs):
        pass

    async def run(self):
        return "receipt"


class TheReportsAreRecordedForTheLead(unittest.IsolatedAsyncioTestCase):
    async def run_with(self, model, investigate_for=0.4):
        task = {**TASK, "access": [{"siteId": "erp", "url": "https://erp.example.test"}],
                "submission": {"kind": "site", "siteId": "erp"}}
        orchestrator = agent.Orchestrator(task, None, {"erp": FakeTab("erp", FakePage())}, model, time.time())
        self.logged = []
        orchestrator.log = self.logged.append
        orchestrator.collect_until = orchestrator.submit_from = time.time() + investigate_for
        orchestrator.deadline = time.time() + 600
        with patch.object(agent, "BrowsingAgent", QuickSubAgent), patch.object(agent, "SubmissionAgent", Signs), \
                patch.object(agent, "Tab", FakeTab), patch.object(agent, "MIN_INVESTIGATION_SECONDS", 0):
            await asyncio.wait_for(orchestrator.run(), timeout=20)
        return orchestrator

    async def test_the_lead_never_writes_facts_and_the_priced_answer_is_submitted(self):
        model = StandInModel([facts_with(["G1", "G2"], [MATCH_1, MATCH_2])])
        orchestrator = await self.run_with(model)
        self.assertTrue(orchestrator.accepted)
        self.assertEqual([p["position"] for p in orchestrator.submitted["positions"]], ["1", "2"])
        self.assertNotIn("add_facts", model.lead_calls)
        self.assertTrue(any('"decision": "bid"' in state for state in model.states))

    async def test_a_bad_update_is_corrected_by_the_recorder(self):
        model = StandInModel([{"bogus": 1}, facts_with(["G1"], [MATCH_1])])
        orchestrator = await self.run_with(model)
        # position 2's group was never searched: it is bid without a material, to be chosen in the form
        self.assertEqual([(p["position"], p["material_number"]) for p in orchestrator.submitted["positions"]],
                         [("1", MATCH_1["material_number"]), ("2", "")])
        self.assertEqual(orchestrator.unrecorded, [])

    async def test_a_report_that_cannot_be_recorded_is_named_to_the_lead_who_adds_the_facts(self):
        # the recording fails twice: the forced call and the plain call after it
        model = StandInModel([RuntimeError("no model")] * 2)
        orchestrator = await self.run_with(model)
        self.assertTrue(any("Not recorded" in state and "erp" in state for state in model.states))
        self.assertIn("add_facts", model.lead_calls)  # without any facts the lead is made to add them
        self.assertTrue(orchestrator.accepted)

    async def test_a_recording_call_that_stalls_is_asked_again(self):
        class StallsOnce(StandInModel):
            stalled = False

            async def chat(self, messages, tools, **kwargs):
                if len(tools) == 1 and not self.stalled:
                    self.stalled = True
                    await asyncio.sleep(3600)
                return await super().chat(messages, tools, **kwargs)

        with patch.object(agent, "RECORD_CALL_SECONDS", 0.3):
            orchestrator = await self.run_with(StallsOnce([facts_with(["G1"], [MATCH_1])]), investigate_for=0.2)
        self.assertEqual([p["position"] for p in orchestrator.submitted["positions"]], ["1", "2"])
        self.assertTrue(any("took too long" in line for line in self.logged))

    async def test_the_submission_waits_for_a_report_that_is_still_being_recorded(self):
        model = StandInModel([facts_with(["G1", "G2"], [MATCH_1, MATCH_2])], recorder_delay=0.8)
        orchestrator = await self.run_with(model, investigate_for=0.2)
        self.assertEqual([p["position"] for p in orchestrator.submitted["positions"]], ["1", "2"])
        self.assertNotIn("add_facts", model.lead_calls)


class NoRounds(unittest.IsolatedAsyncioTestCase):
    """Investigations run in the background: the lead hears of each report as it comes back and starts what depends
    on it at once, instead of waiting for every investigation it started before."""

    async def test_every_investigation_gets_the_same_length(self):
        deadlines = []

        class Notes:
            def __init__(self, model, tabs, credentials, task, context, name, deadline, log):
                deadlines.append(deadline - time.time())

            async def run(self):
                return "report"

        task = {**TASK, "access": [{"siteId": "erp", "url": "https://erp.example.test"}]}
        orchestrator = agent.Orchestrator(task, None, {"erp": FakeTab("erp", FakePage())}, None, time.time())
        orchestrator.log = lambda text: None
        with patch.object(agent, "BrowsingAgent", Notes), patch.object(agent, "Tab", FakeTab):
            await orchestrator.investigate("erp", "first job")
            await orchestrator.investigate("erp", "follow-up")
        self.assertAlmostEqual(deadlines[0], agent.INVESTIGATION_SECONDS, delta=2)
        self.assertAlmostEqual(deadlines[1], agent.INVESTIGATION_SECONDS, delta=2)

    async def test_a_dependent_job_starts_while_a_slow_one_still_runs(self):
        from types import SimpleNamespace as NS
        events = []

        class Timed:
            def __init__(self, model, tabs, credentials, task, context, name, deadline, log):
                self.task = task

            async def run(self):
                await asyncio.sleep(1.5 if "slow" in self.task else 0.1)
                return f"report for {self.task.splitlines()[0]}"

            def _report(self, text):
                return text

        class Lead:
            def __init__(self):
                self.turn = 0

            async def chat(self, messages, tools, **kwargs):
                if len(tools) == 1:  # the recorder
                    return tool_call("add_facts", {"facts": {}})
                self.turn += 1
                if self.turn == 1:
                    calls = [("investigate", {"site": "erp", "task": "slow job"}),
                             ("investigate", {"site": "erp", "task": "spec job"})]
                elif "report for spec job" in messages[-1]["content"] and not events:
                    events.append(("dependent started", [t for _, _, t in orchestrator.jobs.values()]))
                    calls = [("investigate", {"site": "erp", "task": "dependent job"})]
                else:
                    return NS(content="waiting", tool_calls=[])
                return NS(content="", tool_calls=[NS(id=f"c{i}", function=NS(name=n, arguments=json.dumps(a)))
                                                  for i, (n, a) in enumerate(calls)])

        task = {**TASK, "access": [{"siteId": "erp", "url": "https://erp.example.test"}],
                "submission": {"kind": "site", "siteId": "erp"}}
        orchestrator = agent.Orchestrator(task, None, {"erp": FakeTab("erp", FakePage())}, Lead(), time.time())
        orchestrator.log = lambda text: None
        orchestrator.collect_until = orchestrator.submit_from = time.time() + 30
        with patch.object(agent, "BrowsingAgent", Timed), patch.object(agent, "Tab", FakeTab), \
                patch.object(agent, "MIN_INVESTIGATION_SECONDS", 0), patch.object(agent, "RECORD_WAIT_SECONDS", 0.1):
            try:
                await asyncio.wait_for(orchestrator.run(), timeout=1.2)
            except TimeoutError:
                pass
        self.assertEqual(len(events), 1, "the lead was not told of the quick report before the slow job ended")
        self.assertIn("slow job", " ".join(events[0][1]))  # the slow job was still running at that moment

class TheRecorderModel(unittest.IsolatedAsyncioTestCase):
    async def test_the_recorder_asks_its_own_model(self):
        asked = []

        class Named:
            def __init__(self, name):
                self.name = name

            async def chat(self, messages, tools, **kwargs):
                asked.append(self.name)
                return tool_call("add_facts", {"facts": facts_with(["G1"], [MATCH_1])})

        orchestrator = agent.Orchestrator(TASK, None, {"erp": None}, Named("lead"), time.time(),
                                          worker_model=Named("worker"), recorder_model=Named("recorder"))
        orchestrator.log = lambda text: None
        await orchestrator.record("erp", "report")
        self.assertEqual(asked, ["recorder"])
        self.assertEqual(len(orchestrator.builder.data["materials"]), 1)


class AWebsiteThatDoesNotAnswerAtTheStart(unittest.IsolatedAsyncioTestCase):
    class Page:
        def __init__(self, failures):
            self.failures, self.attempts, self.url = failures, 0, "about:blank"
            self.context = self

        async def goto(self, url, **kwargs):
            self.attempts += 1
            if self.attempts <= self.failures:
                raise TimeoutError("Page.goto: Timeout exceeded")
            self.url = url

        def on(self, *args):
            pass

    async def test_it_is_tried_again_and_the_run_goes_on(self):
        page = self.Page(failures=1)
        tab = await agent.open_site("mail", "https://mail.example.test/inbox", page)
        self.assertEqual(page.attempts, 2)
        self.assertEqual(tab.home, "https://mail.example.test")

    async def test_a_site_that_never_opens_still_gets_its_tab(self):
        page = self.Page(failures=99)
        tab = await agent.open_site("mail", "https://mail.example.test/inbox", page)
        self.assertEqual(page.attempts, agent.START_ATTEMPTS)
        self.assertEqual(tab.home, "https://mail.example.test")
        self.assertEqual(agent.Tab.homes["mail"], "https://mail.example.test")


if __name__ == "__main__":
    unittest.main()
