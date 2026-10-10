"""Offline regressions for research dependencies, partial bids and observed acceptance."""
import asyncio
import json
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_lead import (FakePage, FakeTab, MATCH_1, MATCH_2, Signs, TASK,
                       facts_with, lead, material, priced, tool_call)
import agent


class PartialResearch(unittest.TestCase):
    def test_partial_bid_keeps_original_positions_and_real_erp_tasks(self):
        data = facts_with(["G1"], [material("UNSUITABLE", "G1", 50, 999, 7)])
        orchestrator = lead()
        result = priced(orchestrator.add(data))
        self.assertEqual(result["answer"]["positions"], [])
        self.assertEqual(len(orchestrator.priced.facts.positions), 2)
        self.assertEqual(len(orchestrator.priced.lines), 2)
        self.assertTrue(any(n.get("catalogue_group") == "G2" for n in result["needed"]))
        self.assertFalse(any("number" in n["what"] and "not usable" in n["what"] for n in result["needed"]))
        updated = priced(orchestrator.add({"materials": [MATCH_1]}))
        self.assertEqual([p["position"] for p in updated["answer"]["positions"]], ["1"])
        self.assertEqual(updated["answer"]["total_net"], 400)
        self.assertTrue(any(n.get("catalogue_group") == "G2" for n in updated["needed"]))

    def test_partial_computation_keeps_prices_of_a_third_matching_line(self):
        data = facts_with(["G1"], [material("UNSUITABLE", "G1", 50, 999, 7)])
        data["positions"].append({"position": "3", "quantity": 3, "requirement": "R3"})
        data["revisions"][0]["requirements"].append({"requirement": "R3", "catalogue_group": "G3",
            "bore_mm": 20, "stroke_mm": 100, "pressure_bar": 160, "mounting": "flange"})
        data["materials"].append(material("MATCH-THREE", "G3", 20, 100, 7))
        orchestrator = lead()
        result = priced(orchestrator.add(data))
        self.assertEqual(result["answer"]["total_net"], 21)
        line = next(ln for ln in orchestrator.priced.lines if ln.position == "3")
        self.assertEqual(line.base, 7)
        self.assertEqual(len(orchestrator.priced.lines), 3)
        self.assertTrue(any(n.get("catalogue_group") == "G2" for n in result["needed"]))

    def test_final_no_bid_does_not_require_searching_obsolete_revision_groups(self):
        data = facts_with(["G1", "G2"], [])
        data["revisions"].append({"revision": "OBSOLETE", "current": False, "requirements": [
            {"requirement": "R1", "bore_mm": 50, "stroke_mm": 200, "pressure_bar": 160,
             "mounting": "flange", "catalogue_group": "OLD-GROUP"}]})
        self.assertEqual(priced(lead().add(data))["answer"]["decision"], "no_bid")

    def test_no_bid_requires_a_confirmed_current_revision(self):
        data = facts_with(["G1", "G2"], [material("UNSUITABLE", "G1", 50, 999, 7)])
        data["revisions"][0]["current"] = None
        result = priced(lead().add(data))
        self.assertEqual(result["answer"]["decision"], "bid")
        self.assertTrue(any(n["kind"] == "find" and "current/valid" in n["what"] for n in result["needed"]))

    def test_manual_recovery_acknowledges_only_its_specific_report(self):
        orchestrator = lead()
        first = orchestrator.remember_report("site", "Invented revision report", failed=True)
        second = orchestrator.remember_report("site", "Invented mailbox report", failed=True)
        orchestrator.add(facts_with([], []))
        self.assertEqual(orchestrator.unrecorded, [first, second])
        orchestrator.add({"bogus": 1}, recorded_reports=[first])
        self.assertEqual(orchestrator.unrecorded, [first, second])
        orchestrator.add({"searched_groups": ["G1"]}, recorded_reports=[first])
        self.assertEqual(orchestrator.unrecorded, [second])
        self.assertIn("Invented mailbox report", orchestrator.state())


def batch(entries):
    return NS(content="", tool_calls=[NS(id=f"call-{i}", function=NS(name=name,
        arguments=json.dumps(args))) for i, (name, args) in enumerate(entries)])


class ResearchProgress(unittest.IsolatedAsyncioTestCase):
    async def test_starting_pages_open_together_when_one_website_is_slow(self):
        started = set()
        all_started = asyncio.Event()

        class Page(FakePage):
            def __init__(self, site):
                super().__init__()
                self.site = site

            async def goto(self, url, **kwargs):
                started.add(self.site)
                if len(started) == 4:
                    all_started.set()
                await all_started.wait()

        pairs = [({"siteId": site, "url": f"https://{site}.example.test"}, Page(site))
                 for site in ["source-a", "source-b", "source-c", "source-d"]]
        class StartingTab(FakeTab):
            home = "https://invented.example.test"

        with patch.object(agent, "Tab", StartingTab):
            tabs = await asyncio.wait_for(agent.open_sites(pairs), 1)
        self.assertEqual(set(tabs), started)
        self.assertEqual(len(tabs), 4)

    async def test_follow_up_is_blocked_until_pending_recording_finishes(self):
        orchestrator = lead()
        pending = asyncio.create_task(asyncio.sleep(0.01))
        orchestrator.records.add(pending)
        orchestrator.investigate = AsyncMock(return_value="report")
        self.assertIn("still being recorded", orchestrator.start_investigation("site", "next group"))
        self.assertEqual(orchestrator.jobs, {})
        await pending
        self.assertIn("Started", orchestrator.start_investigation("site", "next group"))
        await asyncio.gather(*(job["future"] for job in orchestrator.jobs.values()))
        self.assertEqual(orchestrator.investigate.await_count, 1)

    async def test_new_facts_cancel_a_decision_based_on_old_requirements(self):
        cancelled = asyncio.Event()

        class SlowPlanner:
            async def chat(self, *args, **kwargs):
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

        orchestrator = lead()
        orchestrator.model = SlowPlanner()
        decision = asyncio.create_task(orchestrator.decision([], orchestrator.tools(), None))
        await asyncio.sleep(0.01)
        orchestrator.add(facts_with([], []))
        self.assertIsNone(await asyncio.wait_for(decision, 1))
        self.assertTrue(cancelled.is_set())
        self.assertTrue(any(n.get("catalogue_group") == "G1" for n in orchestrator.priced.needed()))

    async def check_first_batch(self, interleaved_wait=False):
        sites = ["source-a", "source-b", "source-c", "source-d"]
        started = []
        tasks = []

        class Reader:
            def __init__(self, *args, name, **kwargs):
                self.name = name

            async def run(self):
                started.append(self.name)
                await asyncio.sleep(0.01)
                return "Invented complete report"

        class Planner:
            rounds = 0

            async def chat(self, messages, tools, **kwargs):
                if len(tools) == 1:
                    await asyncio.sleep(0.03)
                    return tool_call("add_facts", {"facts": facts_with(["G1", "G2"], [MATCH_1, MATCH_2])})
                self.rounds += 1
                if self.rounds <= 2:
                    chosen = sites[:3] if self.rounds == 1 else sites
                    entries = [("investigate", {"site": s, "task": "read prerequisite"}) for s in chosen]
                    if interleaved_wait:
                        entries.insert(1, ("wait_for_updates", {"seconds": 0.02}))
                    return batch(entries)
                if not orchestrator.priced:
                    return tool_call("wait_for_updates", {"seconds": 1})
                return tool_call("submit_bid", {})

        task = {**TASK, "access": [{"siteId": s, "url": f"https://{s}.example.test"} for s in sites],
                "submission": {"kind": "site", "siteId": sites[0]}}
        orchestrator = agent.Orchestrator(task, None, {s: FakeTab(s, FakePage()) for s in sites}, Planner(), time.time())
        orchestrator.log = tasks.append
        with patch.object(agent, "BrowsingAgent", Reader), patch.object(agent, "SubmissionAgent", Signs):
            await asyncio.wait_for(orchestrator.run(), 4)
        self.assertCountEqual(started, sites)
        self.assertEqual(len(started), 4)
        self.assertTrue(orchestrator.accepted)
        self.assertEqual(len(orchestrator.submitted["positions"]), 2)

    async def test_first_batch_starts_every_site_before_follow_ups(self):
        await self.check_first_batch()

    async def test_first_batch_starts_all_sites_despite_an_interleaved_wait(self):
        await self.check_first_batch(interleaved_wait=True)

    async def test_background_start_does_not_wait_for_the_other_site(self):
        gate = asyncio.Event()
        orchestrator = lead()
        async def investigate(*args):
            await gate.wait()
            return "report"
        orchestrator.investigate = investigate
        self.assertIn("Started", orchestrator.start_investigation("site", "first prerequisite"))
        self.assertFalse(orchestrator.jobs["job-1"]["future"].done())
        gate.set()
        await orchestrator.jobs["job-1"]["future"]

    async def test_complete_facts_cannot_skip_a_pending_mailbox_investigation(self):
        gate = asyncio.Event()
        attempts = []

        class Planner:
            async def chat(self, messages, tools, **kwargs):
                pending_results = [m["content"] for m in messages if m["role"] == "tool"
                                   and "still pending" in m["content"]]
                if pending_results:
                    self_test.assertFalse(orchestrator.accepted)
                    self_test.assertFalse(orchestrator.research_closed)
                    attempts.extend(pending_results)
                    gate.set()
                    await asyncio.sleep(0.01)
                return tool_call("submit_bid", {})

        self_test = self
        task = {**TASK, "access": [{"siteId": "site", "url": "https://form.example.test"}],
                "submission": {"kind": "site", "siteId": "site"}}
        orchestrator = agent.Orchestrator(task, None, {"site": FakeTab("site", FakePage())}, Planner(), time.time())
        orchestrator.log = lambda text: None
        orchestrator.add(facts_with(["G1", "G2"], [MATCH_1, MATCH_2]))
        self.assertTrue(orchestrator.priced.complete)
        future = asyncio.create_task(gate.wait())
        orchestrator.jobs["mail-job"] = {"site": "site", "task": "read amendments", "status": "running",
                                         "future": future}
        # Do not make the between-turn wait hide the early-submission branch under test.
        with patch.object(agent, "SubmissionAgent", Signs), patch.object(orchestrator, "wait_for_updates",
                                                                        AsyncMock(return_value="pending")):
            await asyncio.wait_for(orchestrator.run(), 2)
        self.assertTrue(attempts)
        self.assertTrue(orchestrator.accepted)

    async def test_rejected_incomplete_submission_keeps_research_open(self):
        class Planner:
            rounds = 0

            async def chat(self, messages, tools, **kwargs):
                self.rounds += 1
                if self.rounds == 2:
                    self_test.assertFalse(orchestrator.research_closed)
                    return tool_call("add_facts", {"facts": {"materials": [MATCH_2], "searched_groups": ["G2"]}})
                return tool_call("submit_bid", {})

        self_test = self
        task = {**TASK, "access": [{"siteId": "site", "url": "https://form.example.test"}],
                "submission": {"kind": "site", "siteId": "site"}}
        orchestrator = agent.Orchestrator(task, None, {"site": FakeTab("site", FakePage())}, Planner(), time.time())
        orchestrator.log = lambda text: None
        orchestrator.add(facts_with(["G1"], [MATCH_1]))
        finished = asyncio.get_running_loop().create_future()
        finished.set_result("Invented initial report")
        orchestrator.jobs["initial"] = {"site": "site", "task": "read", "status": "reported", "future": finished}
        with patch.object(agent, "SubmissionAgent", Signs):
            await asyncio.wait_for(orchestrator.run(), 2)
        self.assertEqual(len(orchestrator.submitted["positions"]), 2)

    async def test_an_early_empty_fallback_cannot_skip_all_research(self):
        class Planner:
            rounds = 0

            async def chat(self, messages, tools, **kwargs):
                self.rounds += 1
                if self.rounds == 1:
                    return tool_call("submit_bid", {"answer": {"decision": "bid", "positions": [], "total_net": 0}})
                self_test.assertFalse(orchestrator.accepted)
                self_test.assertFalse(orchestrator.facts_frozen)
                self_test.assertTrue(any("unchecked fallback" in m.get("content", "") for m in messages))
                raise asyncio.CancelledError

        self_test = self
        task = {**TASK, "submission": {"kind": "api"}}
        orchestrator = lead(task)
        orchestrator.model = Planner()
        with self.assertRaises(asyncio.CancelledError):
            await orchestrator.run()
        self.assertIsNone(orchestrator.submitted)

    async def test_final_barrier_drains_records_and_freezes_the_answer_before_signing(self):
        orchestrator = lead()
        orchestrator.add(facts_with([], [MATCH_1]))
        stopped = asyncio.Event()

        async def read():
            try:
                await asyncio.sleep(60)
            finally:
                stopped.set()

        async def record():
            await asyncio.sleep(0.02)
            orchestrator.add({"materials": [MATCH_2], "searched_groups": ["G1", "G2"]}, by_lead=False)

        future = asyncio.create_task(read())
        orchestrator.jobs["late-read"] = {"site": "site", "task": "read", "status": "running", "future": future}
        pending = asyncio.create_task(record())
        orchestrator.records.add(pending)
        orchestrator.deadline = time.time() + orchestrator.signing_reserve + 0.2

        async def submit(answer, hints):
            self.assertTrue(stopped.is_set())
            self.assertTrue(pending.done())
            self.assertTrue(orchestrator.facts_frozen)
            self.assertEqual(len(answer["positions"]), 2)
            before = dict(orchestrator.builder.data)
            self.assertIn("frozen", orchestrator.add({"positions": []}))
            self.assertEqual(orchestrator.builder.data, before)
            orchestrator.submitted, orchestrator.accepted = answer, True
            return "invented receipt"

        orchestrator.submit = submit
        await asyncio.sleep(0)
        await orchestrator.time_is_up([])
        self.assertTrue(orchestrator.accepted)

    async def test_final_barrier_cancels_a_recorder_that_would_eat_the_signing_reserve(self):
        orchestrator = lead()
        orchestrator.add(facts_with(["G1"], [MATCH_1]))
        pending = asyncio.create_task(asyncio.sleep(60))
        orchestrator.records.add(pending)
        orchestrator.deadline = time.time() + orchestrator.signing_reserve
        await orchestrator.finish_research()
        self.assertTrue(pending.cancelled())
        self.assertFalse(orchestrator.pending_work())


class AcceptanceEvidence(unittest.IsolatedAsyncioTestCase):
    async def checked(self, status, body):
        orchestrator = lead()
        orchestrator.token = "invented-run-token"
        with patch.dict(os.environ, {"CHALLENGE_API_URL": "https://api.example.test"}), \
             patch.object(agent.contract, "http", return_value=(status, json.dumps(body))), \
             patch.object(agent, "RUN_CLOSED_CHECKS", 1):
            await orchestrator.run_is_over()
        return orchestrator

    async def test_forbidden_is_not_acceptance_or_proof_of_a_closed_run(self):
        result = await self.checked(403, {"error": "edge refused request"})
        self.assertFalse(result.accepted)
        self.assertFalse(result.closed)

    async def test_unusable_token_stops_work_without_claiming_acceptance(self):
        result = await self.checked(401, {"error": "token revoked"})
        self.assertTrue(result.closed)
        self.assertFalse(result.accepted)

    async def test_cancelled_is_not_accepted(self):
        result = await self.checked(200, {"status": "cancelled"})
        self.assertTrue(result.closed)
        self.assertFalse(result.accepted)

    async def test_positive_platform_status_confirms_acceptance(self):
        result = await self.checked(200, {"status": "submitted"})
        self.assertTrue(result.accepted)


if __name__ == "__main__":
    unittest.main()
