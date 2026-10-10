"""The simple concept (simple.py): three jobs record typed facts, the engine prices them, the bid is submitted.
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
from browser import Tab  # noqa: E402
from browsing import BrowsingAgent  # noqa: E402

TASK = {"access": [{"siteId": s, "url": f"https://{s}.example.test", "credentials": {}}
                   for s in ("tender-portal", "customer-portal", "erp", "mail")],
        "brief": "Tender T-1, scenario date 2030-01-01.", "answer_schema": {}, "submission": {"kind": "site",
                                                                                              "siteId": "tender-portal"}}
SOURCE = {"site": "x", "document": "d", "date": "2029-12-01"}
POSITIONS = {"positions": [{"position": "1", "quantity": 4, "requirement": "R1", "source": SOURCE},
                           {"position": "2", "quantity": 2, "requirement": "R2", "source": SOURCE}]}
MATERIALS = {"revisions": [{"revision": "A", "current": True, "requirements": [
    {"requirement": "R1", "bore_mm": 50, "stroke_mm": 200, "pressure_bar": 160, "mounting": "flange",
     "catalogue_group": "G1"},
    {"requirement": "R2", "bore_mm": 80, "stroke_mm": 300, "pressure_bar": 160, "mounting": "flange",
     "catalogue_group": "G2"}], "source": SOURCE}],
    "materials": [{"material_number": "M1", "catalogue_group": "G1", "bore_mm": 50, "stroke_mm": 200,
                   "pressure_bar": 160, "mounting": "flange", "active": True, "stock": 10, "stock_lead_weeks": 1,
                   "production_lead_weeks": 5, "conditions": [{"valid_from": "2029-01-01", "valid_to": "2030-12-31", "currency": "EUR",
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


class RecordingAgent(unittest.TestCase):
    def test_facts_are_recorded_at_once_and_no_wrap_up_is_written(self):
        Tab.homes.clear()
        tab = Tab("portal", FakePage("https://portal.example.test/"))

        async def observe(offset=0):
            return "a page"
        tab.observe = observe
        saved, offered = [], []

        class Records:
            def __init__(self):
                self.n = 0

            async def chat(self, messages, tools=None, **kwargs):
                self.n += 1
                if tools is None:
                    raise AssertionError("a recording agent needs no wrap-up call")
                offered.append({t["function"]["name"] for t in tools})
                return NS(content="", tool_calls=[call(self.n, "record", {"facts": {"positions": [{"n": self.n}]}})])

        agent_ = BrowsingAgent(Records(), {"portal": tab}, {}, "task", "context", name="positions",
                               deadline=time.time() + 60, max_steps=3, log=lambda *a: None,
                               record=lambda facts: saved.append(facts) or "Recorded.", record_schema={"type": "object"})
        report = asyncio.run(agent_.run())
        self.assertEqual(saved, [{"positions": [{"n": 1}]}, {"positions": [{"n": 2}]}, {"positions": [{"n": 3}]}])
        self.assertIn("record", offered[0])
        self.assertNotIn("note", offered[0])
        self.assertIn("everything recorded is saved", report)


class Recorder(unittest.TestCase):
    def setUp(self):
        self.lead = agent.Orchestrator(TASK, None, {s["siteId"]: None for s in TASK["access"]}, None, time.time())
        self.lead.log = lambda *a: None

    def test_a_job_records_only_its_own_fields(self):
        record = simple.recorder(self.lead, "positions", set(simple.JOBS["positions"][0]))
        self.assertTrue(record({"tender_reference": "T-1", **POSITIONS}).startswith("Recorded."))
        with self.assertRaises(ValueError):
            record(MATERIALS)
        self.assertEqual(len(self.lead.builder.data["positions"]), 2)

    def test_a_bad_record_is_refused_with_the_reason_and_changes_nothing(self):
        record = simple.recorder(self.lead, "materials", set(simple.JOBS["materials"][0]))
        with self.assertRaises(ValueError) as refused:
            record({"materials": [{"material_number": "M1", "stock": "a lot"}]})
        self.assertIn("stock", str(refused.exception))
        self.assertNotIn("materials", self.lead.builder.data)

    def test_every_job_has_a_schema_with_just_its_fields(self):
        for job, (names, _) in simple.JOBS.items():
            self.assertEqual(set(simple.schema_for(names)["properties"]), set(names), job)


class ThreeJobsThenTheBid(unittest.TestCase):
    def test_what_the_jobs_record_is_priced_and_submitted(self):
        records = {"positions": {"tender_reference": "T-1", "scenario_date": "2030-01-01", **POSITIONS},
                   "materials": MATERIALS, "rules": {"bidder_domain": "bidder.example.test"}}
        started_jobs = []

        class Job:
            def __init__(self, model, tabs, credentials, task, context, name, deadline, max_steps, log, record,
                         record_schema):
                self.name, self.record, self.tabs = name, record, tabs
                started_jobs.append((name, time.time(), sorted(tabs)))

            async def run(self):
                await asyncio.sleep(0.05)
                self.record(records[self.name])
                return "done"

        class Signs:
            entered = None

            def __init__(self, model, tabs, credentials, task, context, deadline, log):
                Signs.entered = task

            async def run(self):
                return "receipt"

        async def scenario():
            pages = {s["siteId"]: FakePage(s["url"]) for s in TASK["access"]}
            tabs = {site: NS(site_id=site, page=page) for site, page in pages.items()}
            lead = agent.Orchestrator(TASK, None, tabs, None, time.time())
            lead.log = lambda *a: None
            lead.collect_until = time.time() + 1

            async def take_tab(site):
                return NS(site_id=site, page=pages[site])
            lead.take_tab = take_tab
            with patch.object(simple, "BrowsingAgent", Job), patch.object(agent, "SubmissionAgent", Signs):
                await simple.run_jobs(lead, None, "T-1")
                await simple.submit(lead)
            return lead

        lead = asyncio.run(scenario())
        self.assertEqual([name for name, _, _ in started_jobs], ["positions", "materials", "rules"])
        self.assertLess(max(t for _, t, _ in started_jobs) - min(t for _, t, _ in started_jobs), 0.5)  # side by side
        self.assertTrue(all(sites == sorted(s["siteId"] for s in TASK["access"]) for _, _, sites in started_jobs))
        self.assertTrue(lead.accepted)  # a local run takes one attempt as the end
        positions = {p["position"]: p for p in lead.submitted["positions"]}
        self.assertEqual(positions["1"]["material_number"], "M1")
        self.assertEqual(positions["1"]["unit_price"], 12.5)
        self.assertEqual(positions["2"]["material_number"], "")  # G2 not searched: chosen in the form
        self.assertIn("MATERIALS", Signs.entered)


if __name__ == "__main__":
    unittest.main()
