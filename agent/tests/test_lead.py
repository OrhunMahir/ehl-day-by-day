"""The lead's own rules, with invented facts: the run's schedule, how facts are added, and when a "no bid" is final."""
import json
import sys
import time
import unittest
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

    def test_a_scored_run_works_with_the_time_the_platform_gives(self):
        now = time.time()
        self.assertEqual(self.minutes({**TASK, "expires_at": (now + 1200) * 1000}, now), [9.0, 9.0, 20.0])

    def test_a_run_that_expires_later_still_plans_with_twenty_minutes(self):
        now = time.time()
        self.assertEqual(self.minutes({**TASK, "expires_at": (now + 7200) * 1000}, now), [9.0, 9.0, 20.0])
        self.assertEqual(self.minutes(TASK, now), [9.0, 9.0, 20.0])

    def test_a_run_close_to_its_end_plans_with_what_is_left(self):
        now = time.time()
        self.assertEqual(self.minutes({**TASK, "expires_at": (now + 300) * 1000}, now), [2.2, 2.2, 5.0])


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
        self.assertEqual([p["position"] for p in result["answer"]["positions"]], ["1"])
        self.assertEqual(result["omitted"], ["2"])

    def test_a_group_searched_on_purpose_without_a_match_is_a_no_bid(self):
        result = priced(lead().add(facts_with(["G1", "G2"], [MATCH_1, NEAR_MISS_2])))
        self.assertEqual(result["answer"]["decision"], "no_bid")
        self.assertEqual(result["answer"]["positions"], [])

    def test_with_every_match_found_all_positions_are_bid(self):
        result = priced(lead().add(facts_with(["G1", "G2"], [MATCH_1, NEAR_MISS_2, MATCH_2])))
        self.assertEqual(result["answer"]["decision"], "bid")
        self.assertEqual(result["omitted"], [])


if __name__ == "__main__":
    unittest.main()
