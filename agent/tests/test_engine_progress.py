"""Incremental reading must preserve work and never turn unknown ERP data into a conclusive answer.

All records and numbers in these tests are invented.
"""
import copy
import unittest
from dataclasses import replace
from decimal import Decimal as D

from test_engine_base import core_facts

from engine import evaluate
from facts import FactsBuilder, to_dict


def find_needs(result):
    return [need for need in result.needed() if need["kind"] == "find"]


class CompletedSearches(unittest.TestCase):
    def test_one_unsuitable_candidate_does_not_prove_no_matching_material_exists(self):
        f = core_facts()
        f.searched_groups = ["G-B"]
        f.materials = [m for m in f.materials if m.material_number != "M-A1"]
        result = evaluate(f)
        self.assertEqual(result.answer["decision"], "bid")
        self.assertEqual(result.omitted, ["1"])
        self.assertTrue(any(n.get("catalogue_group") == "G-A" for n in find_needs(result)))

    def test_complete_search_without_any_active_match_still_proves_no_bid(self):
        f = core_facts()
        f.materials = [m for m in f.materials if m.material_number != "M-A1"]
        result = evaluate(f)
        self.assertEqual((result.answer["decision"], result.answer["positions"], result.answer["total_net"]),
                         ("no_bid", [], 0))

    def test_unknown_status_is_neither_an_eligible_match_nor_a_conclusive_no_bid(self):
        f = core_facts()
        f.materials[0] = replace(f.materials[0], active=None)
        result = evaluate(f)
        self.assertEqual(result.answer["decision"], "bid")
        self.assertEqual(result.omitted, ["1"])
        self.assertIsNone(result.lines[0].material)
        status = next(n for n in find_needs(result) if "status (active/released?)" in n["what"])
        self.assertEqual((status["site"], status["positions"], status["catalogue_group"]), ("ERP", ["1"], "G-A"))
        self.assertFalse(result.complete)
        f.materials[0].active = True
        self.assertEqual([p["position"] for p in evaluate(f).answer["positions"]], ["1", "2"])
        f.materials[0].active = False
        self.assertEqual(evaluate(f).answer["decision"], "no_bid")

    def test_a_known_active_match_is_chosen_over_an_unknown_status_match(self):
        f = core_facts()
        f.materials.append(replace(f.materials[0], material_number="M-A0-UNKNOWN", active=None))
        result = evaluate(f)
        self.assertEqual(result.answer["positions"][0]["material_number"], "M-A1")
        self.assertFalse(any("status (active/released?)" in n["what"] for n in find_needs(result)))


class EarlyGroupDependencies(unittest.TestCase):
    def test_groups_are_available_before_tender_positions_and_attributes(self):
        f = core_facts()
        f.positions = []
        f.materials = []
        f.searched_groups = []
        f.revisions[1].requirements[0].bore_mm = None
        needs = find_needs(evaluate(f))
        searches = [n for n in needs if n.get("catalogue_group")]
        self.assertEqual({n["catalogue_group"] for n in searches}, {"G-A", "G-B"})
        self.assertTrue(all(n["site"] == "ERP" and "positions" not in n for n in searches))
        self.assertTrue(any(n["site"] == "tender portal" for n in needs))

    def test_later_positions_enrich_the_same_group_need_without_duplicates(self):
        f = core_facts()
        f.materials = []
        f.searched_groups = []
        f.revisions[1].requirements[0].bore_mm = None
        f.positions.append(replace(f.positions[0], position="3"))
        needs = find_needs(evaluate(f))
        searches = [n for n in needs if n.get("catalogue_group") == "G-A"]
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0]["positions"], ["1", "3"])

    def test_only_the_governing_revision_and_known_positions_create_group_work(self):
        f = core_facts()
        f.materials = []
        f.searched_groups = []
        f.positions = f.positions[:1]
        f.revisions[0].requirements[0].catalogue_group = "OLD-GROUP"
        searches = [n["catalogue_group"] for n in find_needs(evaluate(f)) if n.get("catalogue_group")]
        self.assertEqual(searches, ["G-A"])
        f.positions[0].requirement = "UNKNOWN-REQUIREMENT"
        self.assertFalse(any(n.get("catalogue_group") for n in find_needs(evaluate(f))))

    def test_group_search_does_not_wait_for_a_usable_travel_reserve(self):
        f = core_facts()
        f.materials = []
        f.searched_groups = []
        f.revisions[1].requirements[0] = replace(f.revisions[1].requirements[0], usable_travel=True,
                                                end_reserve_key="INVENTED-RESERVE")
        needs = find_needs(evaluate(f))
        self.assertTrue(any(n.get("catalogue_group") == "G-A" for n in needs))
        self.assertTrue(any(n["site"] == "mailbox" and "end reserve" in n["what"] for n in needs))


class IncrementalPriceConditions(unittest.TestCase):
    def condition(self, start="2030-01-01", end="2030-12-31", quantity=1, price=12):
        return {"valid_from": start, "valid_to": end,
                "scales": [{"min_quantity": quantity, "price": price}]}

    def add(self, builder, conditions):
        builder.add({"materials": [{"material_number": "INVENTED-M", "conditions": conditions}]})

    def test_later_condition_pages_keep_earlier_validity_windows(self):
        b = FactsBuilder()
        self.add(b, [self.condition()])
        self.add(b, [self.condition(start="2029-01-01", end="2029-12-31", price=99)])
        self.assertEqual(len(b.data["materials"][0]["conditions"]), 2)
        self.assertEqual(b.data["materials"][0]["conditions"][0]["scales"][0]["price"], 12)

    def test_later_expired_condition_page_does_not_replace_the_current_price(self):
        b = FactsBuilder(to_dict(core_facts()))
        b.add({"materials": [{"material_number": "M-A1", "conditions": [
            self.condition(start="2029-01-01", end="2029-12-31", price=999)]}]})
        result = evaluate(b.facts())
        self.assertEqual(result.answer["positions"][0]["base_unit_price"], 90)
        self.assertEqual(result.answer["total_net"], 3807.36)

    def test_a_later_high_quantity_scale_keeps_the_lower_scale_the_bid_reaches(self):
        b = FactsBuilder(to_dict(core_facts()))
        b.add({"materials": [{"material_number": "M-A1", "conditions": [
            self.condition(quantity=50, price=75)]}]})
        result = evaluate(b.facts())
        self.assertEqual(result.answer["positions"][0]["base_unit_price"], 90)
        self.assertEqual(result.answer["positions"][0]["unit_price"], 86.4)

    def test_scale_pages_merge_and_same_threshold_updates_without_a_duplicate(self):
        b = FactsBuilder()
        self.add(b, [self.condition()])
        self.add(b, [self.condition(start="01.01.2030", end="31.12.2030", quantity="10.0", price=9)])
        self.add(b, [self.condition(quantity=10, price=8)])
        condition = b.data["materials"][0]["conditions"][0]
        self.assertEqual(len(b.data["materials"][0]["conditions"]), 1)
        self.assertEqual([D(str(s["min_quantity"])) for s in condition["scales"]], [D(1), D(10)])
        self.assertEqual([s["price"] for s in condition["scales"]], [12, 8])

    def test_partial_condition_metadata_and_empty_lists_do_not_erase_scales(self):
        b = FactsBuilder()
        self.add(b, [self.condition()])
        self.add(b, [{"valid_from": "2030-01-01", "valid_to": "2030-12-31", "price_unit": 100,
                      "currency": "USD", "fx_key": "INVENTED-FX", "scales": []}])
        self.add(b, [])
        condition = b.data["materials"][0]["conditions"][0]
        self.assertEqual((condition["currency"], condition["price_unit"], condition["fx_key"]),
                         ("USD", 100, "INVENTED-FX"))
        self.assertEqual(len(condition["scales"]), 1)

    def test_bad_nested_update_rejects_all_changes_without_mutating_previous_facts(self):
        b = FactsBuilder()
        self.add(b, [self.condition()])
        before = b.data
        saved = copy.deepcopy(before)
        with self.assertRaises(ValueError):
            self.add(b, [self.condition(quantity=10, price="not a price")])
        self.assertIs(b.data, before)
        self.assertEqual(b.data, saved)

    def test_explicit_material_snapshot_can_remove_a_wrong_condition(self):
        b = FactsBuilder()
        self.add(b, [self.condition()])
        b.add({"replace_lists": ["materials"],
               "materials": [{"material_number": "INVENTED-M", "conditions": []}]})
        self.assertEqual(b.data["materials"][0]["conditions"], [])


if __name__ == "__main__":
    unittest.main()
