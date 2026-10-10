"""Facts recorded at the source and work started by what is missing (docs/plan-facts-at-source.md), with invented
facts and stand-ins for the models and the browser."""
import asyncio
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent  # noqa: E402
from browsing import BrowsingAgent, tool_schemas  # noqa: E402

SITES = [{"siteId": "spec", "url": "https://spec.test/"}, {"siteId": "erp", "url": "https://erp.test/"}]
TASK = {"access": SITES, "brief": "", "answer_schema": {}, "submission": {}}
BRIEF_FACTS = {"tender_reference": "T-1", "scenario_date": "2030-01-01", "director_address": "boss@bidder.test",
               "bidder_domain": "bidder.test"}
SPEC = {"positions": [{"position": "1", "quantity": 4, "requirement": "R1"}],
        "revisions": [{"revision": "A", "current": True, "requirements": [
            {"requirement": "R1", "bore_mm": 50, "stroke_mm": 200, "pressure_bar": 160, "mounting": "flange",
             "catalogue_group": "G1"}]}]}
MATERIAL = {"material_number": "M1", "active": True, "catalogue_group": "G1", "bore_mm": 50, "stroke_mm": 200,
            "pressure_bar": 160, "mounting": "flange", "stock": 10, "stock_lead_weeks": 1, "production_lead_weeks": 5,
            "conditions": [{"valid_from": "2029-01-01", "valid_to": "2031-01-01",
                            "scales": [{"min_quantity": 1, "price": 100}]}]}


class Call:
    def __init__(self, name, arguments):
        self.id, self.function = f"c-{name}", type("F", (), {"name": name, "arguments": json.dumps(arguments)})()


class Assigner:
    """The lead model as the dispatcher asks it: every listed need goes to `site`."""

    def __init__(self, site="erp"):
        self.site, self.asked = site, 0

    async def chat(self, messages, tools=None, **kwargs):
        self.asked += 1
        numbers = [line.split(":")[0][2:] for line in messages[-1]["content"].splitlines() if line.startswith("- n")]
        rows = [{"need": n, "site": self.site, "task": "Search it and record what you read."} for n in numbers]
        return type("M", (), {"content": "", "tool_calls": [Call("assign", {"assignments": rows})]})()


def lead(model=None, collect_seconds=120.0):
    orchestrator = agent.Orchestrator(TASK, None, {"spec": None, "erp": None}, model or Assigner(), time.time())
    orchestrator.log = lambda text: None
    orchestrator.collect_until = time.time() + collect_seconds
    return orchestrator


class RecordingAtTheSource(unittest.TestCase):
    def test_a_record_is_added_at_once_and_wakes_the_dispatcher(self):
        orchestrator = lead()
        answer = orchestrator.record_at_source({**BRIEF_FACTS, **SPEC})
        self.assertTrue(answer.startswith("Recorded."))
        self.assertTrue(orchestrator.facts_changed.is_set())
        self.assertEqual(orchestrator.builder.data["positions"][0]["position"], "1")

    def test_a_bad_record_names_the_field_and_keeps_earlier_facts(self):
        orchestrator = lead()
        orchestrator.record_at_source(BRIEF_FACTS)
        answer = orchestrator.record_at_source({"materials": [{"material_number": "M1", "stock": "viele"}]})
        self.assertIn("Material.stock", answer)
        self.assertEqual(orchestrator.builder.data["tender_reference"], "T-1")

    def test_the_report_only_fills_gaps_after_recording_at_the_source(self):
        orchestrator = lead()
        orchestrator.record_at_source({**BRIEF_FACTS, "materials": [{**MATERIAL, "stock": 10}]})
        orchestrator.add({"materials": [{**MATERIAL, "stock": 99, "setup_fee_eur": 5}]}, by_lead=False,
                         prefer_existing=True)
        material = orchestrator.builder.data["materials"][0]
        self.assertEqual((material["stock"], material["setup_fee_eur"]), (10, 5))


class RecordersNeverTakeRecordsBack(unittest.TestCase):
    """A recorder that sent `replace_lists` emptied a local run's positions, revisions and materials in one call."""

    def full(self):
        orchestrator = lead()
        orchestrator.record_at_source({**BRIEF_FACTS, **SPEC, "materials": [MATERIAL]})
        return orchestrator

    def test_the_report_recorder_cannot_empty_lists(self):
        orchestrator = self.full()
        orchestrator.add({"replace_lists": ["positions", "revisions", "materials"], "discount_approvals": []},
                         by_lead=False)
        data = orchestrator.builder.data
        self.assertEqual((len(data["positions"]), len(data["revisions"]), len(data["materials"])), (1, 1, 1))

    def test_a_sub_agent_cannot_empty_lists(self):
        orchestrator = self.full()
        orchestrator.record_at_source({"replace_lists": ["materials"]})
        self.assertEqual(len(orchestrator.builder.data["materials"]), 1)

    def test_the_lead_still_can_take_records_back(self):
        orchestrator = self.full()
        orchestrator.add({"replace_lists": ["materials"]})
        self.assertEqual(orchestrator.builder.data["materials"], [])

    def test_an_empty_list_does_not_erase_known_conditions(self):
        orchestrator = self.full()
        orchestrator.record_at_source({"materials": [{"material_number": "M1", "conditions": [], "stock": 3}]})
        material = orchestrator.builder.data["materials"][0]
        self.assertEqual((len(material["conditions"]), material["stock"]), (1, 3))


class TheChecklistBeforeTheBrief(unittest.TestCase):
    def test_needs_are_known_before_reference_and_date_are_recorded(self):
        orchestrator = lead()
        orchestrator.builder.add(SPEC)
        needs = orchestrator.open_needs()
        self.assertTrue(any(n["what"].startswith("search Kataloggruppe G1") for n in needs))
        self.assertIsNone(orchestrator.priced)  # a checklist, never a price


class Dispatching(unittest.IsolatedAsyncioTestCase):
    async def run_dispatch(self, orchestrator, seconds=0.6):
        task = asyncio.ensure_future(orchestrator.dispatch())
        orchestrator.facts_changed.set()
        await asyncio.sleep(seconds)
        task.cancel()

    async def test_a_new_group_search_is_started_once_and_resolved_by_what_is_recorded(self):
        orchestrator = lead()
        orchestrator.builder.add({**BRIEF_FACTS, **SPEC})
        started = []

        async def investigate(site, task, need=None):
            started.append((site, need, task))
            orchestrator.record_at_source({"materials": [MATERIAL], "searched_groups": ["G1"]})
            return "report"
        orchestrator.investigate = investigate
        await self.run_dispatch(orchestrator)
        self.assertEqual(len(started), 1)
        site, need, task = started[0]
        self.assertEqual(site, "erp")
        self.assertIn("search Kataloggruppe G1", need)
        self.assertIn("Match against: requirement R1: bore 50", task)
        self.assertIn("resolved", orchestrator.timeline[need])

    async def test_survey_needs_wait_for_the_first_round(self):
        orchestrator = lead(Assigner("spec"))
        orchestrator.builder.add(BRIEF_FACTS)
        started = []

        async def investigate(site, task, need=None):
            started.append(need)
            await asyncio.sleep(5)
        orchestrator.investigate = investigate
        await self.run_dispatch(orchestrator, 0.3)
        self.assertEqual(started, [])
        orchestrator.first_round_done = True
        await self.run_dispatch(orchestrator, 0.3)
        self.assertTrue(started and all(n.startswith("find|") for n in started))

    async def test_a_need_no_investigation_resolves_is_handed_out_at_most_twice(self):
        orchestrator = lead()
        orchestrator.builder.add({**BRIEF_FACTS, **SPEC})
        started = []

        async def investigate(site, task, need=None):
            started.append(need)
            return "nothing found"
        orchestrator.investigate = investigate
        with patch.object(agent, "DISPATCH_IDLE_SECONDS", 0.05):
            await self.run_dispatch(orchestrator, 1.0)
        searches = [n for n in started if "search Kataloggruppe G1" in n]
        self.assertEqual(len(searches), agent.ATTEMPTS_PER_NEED)

    async def test_needs_for_the_brief_are_never_dispatched(self):
        orchestrator = lead()
        orchestrator.builder.add({**SPEC, "tender_reference": "T-1", "scenario_date": "2030-01-01"})
        started = []

        async def investigate(site, task, need=None):
            started.append(need)
        orchestrator.investigate = investigate
        await self.run_dispatch(orchestrator)
        self.assertFalse(any("|brief|" in n for n in started))

    async def test_nothing_is_started_just_before_the_investigations_end(self):
        orchestrator = lead(collect_seconds=agent.DISPATCH_LAST_SECONDS - 5)
        orchestrator.builder.add({**BRIEF_FACTS, **SPEC})
        started = []

        async def investigate(site, task, need=None):
            started.append(need)
        orchestrator.investigate = investigate
        await self.run_dispatch(orchestrator)
        self.assertEqual(started, [])

    async def test_an_assignment_to_an_unknown_site_or_need_is_dropped(self):
        class Confused(Assigner):
            async def chat(self, messages, tools=None, **kwargs):
                rows = [{"need": "n1", "site": "nowhere", "task": "x"}, {"need": "n99", "site": "erp", "task": "x"},
                        {"need": "n1", "site": "erp", "task": ""}]
                return type("M", (), {"content": "", "tool_calls": [Call("assign", {"assignments": rows})]})()
        orchestrator = lead(Confused())
        orchestrator.builder.add({**BRIEF_FACTS, **SPEC})
        self.assertEqual(await orchestrator.assign(orchestrator.open_needs()), [])


class ContextForATask(unittest.TestCase):
    def test_a_stroke_waiting_for_its_end_reserve_is_not_used_to_filter(self):
        orchestrator = lead()
        spec = json.loads(json.dumps(SPEC))
        spec["revisions"][0]["requirements"][0].update({"usable_travel": True, "end_reserve_key": "E1"})
        orchestrator.builder.add({**BRIEF_FACTS, **spec})
        need = next(n for n in orchestrator.open_needs() if n["what"].startswith("search Kataloggruppe G1"))
        self.assertIn("do not filter by stroke", orchestrator.context_for(need))


class TheRecordToolOfABrowsingAgent(unittest.IsolatedAsyncioTestCase):
    class Tab:
        site_id, home = "erp", "https://erp.test"
        page = type("P", (), {"url": "https://erp.test/list"})()

        async def observe(self):
            return "[erp] a result list"

    async def test_records_go_to_the_recorder_and_count_as_notes(self):
        received = []

        def recorder(update):
            received.append(update)
            return "Recorded. facts so far: materials: 1"

        class Model:
            n = 0

            async def chat(self, messages, tools=None, **kwargs):
                self.n += 1
                if self.n == 1:
                    return type("M", (), {"content": "", "tool_calls": [
                        Call("record", {"facts": {"materials": [{"material_number": "M1"}]}})]})()
                return type("M", (), {"content": "", "tool_calls": [Call("finish", {"report": "FOUND: M1"})]})()

        agent_ = BrowsingAgent(Model(), {"erp": self.Tab()}, {}, "task", "context", name="erp",
                               deadline=time.time() + 120, recorder=recorder, field_reference="- materials: ...")
        report = await agent_.run()
        self.assertEqual(received, [{"materials": [{"material_number": "M1"}]}])
        self.assertEqual(agent_.records, 1)
        self.assertIn("(recorded) Recorded.", report)

    def test_the_record_tool_is_offered_only_with_a_recorder(self):
        names = lambda recording: {t["function"]["name"] for t in tool_schemas(False, recording)}
        self.assertIn("record", names(True))
        self.assertNotIn("record", names(False))


if __name__ == "__main__":
    unittest.main()
