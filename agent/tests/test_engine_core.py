"""Core bid rules (1-8, 13, 15-17) on invented data. See test_engine_base.py for the hand calculation."""
import unittest
from dataclasses import replace
from decimal import Decimal as D

from test_engine_base import DIRECTOR, REF, at, condition, core_facts

from engine import build_bid, evaluate
from facts import DiscountApproval, QuantityChange, TenderPosition, Unit


def pos(answer, number):
    return next(p for p in answer["positions"] if p["position"] == number)


class CoreBid(unittest.TestCase):
    def test_full_core_answer(self):
        answer = build_bid(core_facts())
        self.assertEqual(answer, {
            "tender_reference": REF, "decision": "bid", "currency": "EUR", "discount_percent": 4,
            "positions": [
                {"position": "1", "material_number": "M-A1", "quantity": 12, "unit_price": 86.4,
                 "lead_time_weeks": 1, "base_unit_price": 90, "surcharge_per_unit": 0, "packaging_per_unit": 0,
                 "rebate_total": 0},
                {"position": "2", "material_number": "M-B1", "quantity": 12, "unit_price": 230.88,
                 "lead_time_weeks": 8, "base_unit_price": 240.5, "surcharge_per_unit": 0, "packaging_per_unit": 0,
                 "rebate_total": 0},
            ],
            "total_net": 3807.36,
        })

    def test_total_is_sum_of_rounded_lines(self):
        answer = build_bid(core_facts())
        total = sum(D(str(p["unit_price"])) * p["quantity"] for p in answer["positions"])
        self.assertEqual(D(str(answer["total_net"])), total)


class Quantity(unittest.TestCase):
    def test_latest_remaining_change_after_withdrawal(self):           # rules 1, 13
        self.assertEqual(pos(build_bid(core_facts()), "1")["quantity"], 12)

    def test_without_withdrawal_latest_change_wins(self):              # rule 1
        f = core_facts()
        f.withdrawals = []
        self.assertEqual(pos(build_bid(f), "1")["quantity"], 20)

    def test_all_changes_withdrawn_falls_back_to_tender(self):         # rule 13
        f = core_facts()
        f.quantity_changes = [QuantityChange("1", D(20), "Q2", at("2030-02-05"))]
        self.assertEqual(pos(build_bid(f), "1")["quantity"], 10)

    def test_set_converted_with_governing_spec(self):                  # rule 17
        self.assertEqual(pos(build_bid(core_facts()), "2")["quantity"], 12)

    def test_set_change_in_q_and_a_is_in_sets(self):                   # rule 17
        f = core_facts()
        f.quantity_changes.append(QuantityChange("2", D(5), "Q4", at("2030-02-20")))
        self.assertEqual(pos(build_bid(f), "2")["quantity"], 20)

    def test_set_without_pieces_per_set_is_bid_with_the_stated_figure(self):
        # every answer field scores on its own: the position stays in the bid with its material, and the tender's
        # own figure stands in for the pieces until the set size is read
        f = core_facts()
        f.revisions[1].requirements[1] = replace(f.revisions[1].requirements[1], pieces_per_set=None)
        result = evaluate(f)
        self.assertEqual([p["position"] for p in result.answer["positions"]], ["1", "2"])
        self.assertEqual(result.omitted, [])
        self.assertIn("2", result.incomplete)
        self.assertEqual(pos(result.answer, "2")["material_number"], "M-B1")
        self.assertTrue(any("pieces per set" in i for i in result.issues))


class RevisionAndMaterial(unittest.TestCase):
    def test_current_revision_governs(self):                           # rule 2
        # R-1 says stroke 250 for A; only the current R-2 (300) matches M-A1.
        self.assertEqual(pos(build_bid(core_facts()), "1")["material_number"], "M-A1")

    def test_inactive_and_non_matching_materials_skipped(self):        # rule 3
        f = core_facts()
        f.materials = [m for m in f.materials if m.material_number != "M-A1"]
        answer = build_bid(f)
        self.assertEqual(answer["decision"], "no_bid")

    def test_no_material_means_no_bid(self):                           # rules 3, 8
        f = core_facts()
        f.revisions[1].requirements[1] = replace(f.revisions[1].requirements[1], pressure_bar=D(999))
        result = evaluate(f)
        answer = result.answer
        self.assertEqual((answer["decision"], answer["positions"], answer["total_net"]), ("no_bid", [], 0))
        self.assertTrue(any("closest: M-B1: pressure 200 (required 999)" in i for i in result.issues), result.issues)

    def test_no_bid_with_conditional_approval_has_zero_discount(self):  # rule 16
        f = core_facts()
        f.revisions[1].requirements[1] = replace(f.revisions[1].requirements[1], pressure_bar=D(999))
        self.assertEqual(build_bid(f)["discount_percent"], 0)

    def test_certification_must_match(self):                           # rule 3
        f = core_facts()
        f.materials[3] = replace(f.materials[3], certification="EN 1111")
        self.assertEqual(build_bid(f)["decision"], "no_bid")

    def test_required_none_does_not_match_a_certified_material(self):  # rule 3, exact match
        f = core_facts()
        f.materials[0] = replace(f.materials[0], certification="EN 9999")
        self.assertEqual(build_bid(f)["decision"], "no_bid")

    def test_unsearched_group_is_not_a_no_bid(self):
        f = core_facts()
        f.materials = [m for m in f.materials if m.catalogue_group != "G-B"]
        result = evaluate(f)
        self.assertEqual(result.answer["decision"], "bid")
        self.assertEqual([p["position"] for p in result.answer["positions"]], ["1", "2"])
        self.assertEqual(pos(result.answer, "2")["material_number"], "")  # chosen in the form from the requirement
        self.assertIn("2", result.incomplete)
        self.assertTrue(any("not searched" in i for i in result.issues))

    def test_searched_group_without_results_is_a_no_bid(self):  # rule 3
        f = core_facts()
        f.materials = [m for m in f.materials if m.catalogue_group != "G-B"]
        f.searched_groups = ["g-b"]
        self.assertEqual(build_bid(f)["decision"], "no_bid")

    def test_requirement_without_group_is_not_a_no_bid(self):          # rule 10: every requirement names one
        f = core_facts()
        f.revisions[1].requirements[1] = replace(f.revisions[1].requirements[1], catalogue_group="",
                                                 pressure_bar=D(999))
        result = evaluate(f)
        self.assertEqual(result.answer["decision"], "bid")
        self.assertIn("2", result.incomplete)
        self.assertEqual(result.omitted, [])
        self.assertTrue(any(n["what"].startswith("the Kataloggruppe of requirement B") for n in result.needed()))

    def test_certification_spacing_does_not_matter(self):
        f = core_facts()
        f.materials[3] = replace(f.materials[3], certification="en9999")
        self.assertEqual(pos(build_bid(f), "2")["material_number"], "M-B1")

    def test_two_matching_materials_choice_does_not_depend_on_order(self):
        f = core_facts()
        twin = replace(f.materials[0], material_number="M-A0")
        f.materials.append(twin)
        first = pos(build_bid(f), "1")["material_number"]
        f.materials.reverse()
        self.assertEqual(pos(build_bid(f), "1")["material_number"], first)

    def test_incomplete_requirement_is_not_a_no_bid(self):
        f = core_facts()
        f.revisions[1].requirements[0] = replace(f.revisions[1].requirements[0], bore_mm=None)
        result = evaluate(f)
        self.assertEqual(result.answer["decision"], "bid")
        self.assertEqual([p["position"] for p in result.answer["positions"]], ["1", "2"])
        self.assertIn("1", result.incomplete)

    def test_without_current_mark_latest_dated_revision(self):
        f = core_facts()
        f.revisions[1].current = None
        f.revisions[0].current = None
        self.assertEqual(pos(build_bid(f), "1")["material_number"], "M-A1")


class Price(unittest.TestCase):
    def test_condition_valid_on_scenario_date(self):                   # rule 4
        self.assertEqual(pos(build_bid(core_facts()), "1")["base_unit_price"], 90)

    def test_highest_scale_reached(self):                              # rule 4
        f = core_facts()
        f.withdrawals = []          # 20 pieces: still scale 10
        self.assertEqual(pos(build_bid(f), "1")["base_unit_price"], 90)
        f.quantity_changes.append(QuantityChange("1", D(50), "Q5", at("2030-02-25")))
        self.assertEqual(pos(build_bid(f), "1")["base_unit_price"], 80)

    def test_price_unit_normalised(self):                              # rules 4, 22
        self.assertEqual(pos(build_bid(core_facts()), "2")["base_unit_price"], 240.5)

    def test_half_up_rounding_of_unit_price(self):
        f = core_facts()
        # discount 0 %: 10.3125 -> 10.31 and 10.315 -> 10.32 (half up, not half even)
        f.materials[0].conditions[1] = condition((1, "10.3125"))
        f.discount_approvals = [DiscountApproval(REF, D(0), source=at("2030-03-01", DIRECTOR))]
        self.assertEqual(pos(build_bid(f), "1")["unit_price"], 10.31)
        f.materials[0].conditions[1] = condition((1, "10.315"))
        self.assertEqual(pos(build_bid(f), "1")["unit_price"], 10.32)


class Discount(unittest.TestCase):
    def test_latest_exact_authorized_not_future(self):                 # rules 5, 21
        self.assertEqual(build_bid(core_facts())["discount_percent"], 4)

    def test_threshold_is_inclusive(self):                             # rule 16
        f = core_facts()
        f.discount_approvals[1] = replace(f.discount_approvals[1], min_order_value=D("3966.00"))
        self.assertEqual(build_bid(f)["discount_percent"], 4)

    def test_failed_threshold_gives_zero_not_older_approval(self):     # rule 16
        f = core_facts()
        f.discount_approvals[1] = replace(f.discount_approvals[1], min_order_value=D("3966.01"))
        answer = build_bid(f)
        self.assertEqual(answer["discount_percent"], 0)
        self.assertEqual(pos(answer, "1")["unit_price"], 90)

    def test_no_approval_means_zero(self):                             # rule 5
        f = core_facts()
        f.discount_approvals = [a for a in f.discount_approvals if a.min_order_value is None and a.percent == 9]
        self.assertEqual(build_bid(f)["discount_percent"], 0)

    def test_threshold_with_a_position_not_priced_yet_is_flagged(self):   # rule 16
        f = core_facts()
        f.materials = [m for m in f.materials if m.catalogue_group != "G-B"]   # position 2 not searched yet
        result = evaluate(f)
        self.assertEqual(result.answer["discount_percent"], 0)                # 12 x 90 = 1080 < 3000, for now
        self.assertTrue(any("may be too low" in i for i in result.issues))
        self.assertTrue(any(n["kind"] == "verify" and "minimum order value" in n["what"] for n in result.needed()))

    def test_threshold_excludes_surcharge_and_setup(self):             # rule 16
        f = core_facts()
        f.materials[0] = replace(f.materials[0], setup_fee_eur=D("500"))
        self.assertEqual(build_bid(f)["discount_percent"], 4)
        f.discount_approvals[1] = replace(f.discount_approvals[1], min_order_value=D("3966.01"))
        self.assertEqual(build_bid(f)["discount_percent"], 0)


class LeadTime(unittest.TestCase):
    def test_stock_or_production(self):                                # rule 6
        answer = build_bid(core_facts())
        self.assertEqual((pos(answer, "1")["lead_time_weeks"], pos(answer, "2")["lead_time_weeks"]), (1, 8))

    def test_shared_stock_sums_positions_of_same_material(self):       # rule 15
        f = core_facts()
        f.positions.append(TenderPosition("3", D(40), Unit.PIECE, "A"))   # 12 + 40 = 52 > stock 50
        answer = build_bid(f)
        self.assertEqual([pos(answer, n)["lead_time_weeks"] for n in ("1", "3")], [6, 6])
        # price scales stay per position
        self.assertEqual([pos(answer, n)["base_unit_price"] for n in ("1", "3")], [90, 90])

    def test_stock_exactly_covering_is_enough(self):                   # rule 15
        f = core_facts()
        f.positions.append(TenderPosition("3", D(38), Unit.PIECE, "A"))   # 12 + 38 = 50
        answer = build_bid(f)
        self.assertEqual([pos(answer, n)["lead_time_weeks"] for n in ("1", "3")], [1, 1])


class Robustness(unittest.TestCase):
    def test_unknown_price_still_lists_position(self):
        f = core_facts()
        f.materials[0].conditions = []
        result = evaluate(f)
        self.assertEqual(result.incomplete, ["1"])
        p = pos(result.answer, "1")
        self.assertEqual((p["material_number"], p["quantity"]), ("M-A1", 12))
        self.assertNotIn("base_unit_price", p)

    def test_positions_ordered_naturally(self):
        f = core_facts()
        f.positions = [TenderPosition("10", D(1), Unit.PIECE, "A"), TenderPosition("9", D(1), Unit.PIECE, "A")]
        self.assertEqual([p["position"] for p in build_bid(f)["positions"]], ["9", "10"])


class AlwaysACompleteAnswer(unittest.TestCase):
    """Every answer field scores on its own, so every position is bid with what is known at any moment; a gap
    costs only its own fields, never the position or the bid."""

    def test_with_only_the_positions_known_every_position_is_bid_with_its_quantity(self):
        f = core_facts()
        f.materials, f.revisions, f.discount_approvals = [], [], []
        result = evaluate(f)
        self.assertEqual(result.answer["decision"], "bid")
        self.assertEqual([(p["position"], p["quantity"], p["material_number"]) for p in result.answer["positions"]],
                         [("1", 12, ""), ("2", 3, "")])  # Q2 is withdrawn, so Q1's 12 counts; 3 sets as stated
        self.assertEqual(result.omitted, [])
        self.assertEqual(sorted(result.incomplete), ["1", "2"])
        self.assertEqual(result.answer["total_net"], 0)

    def test_a_near_miss_is_bid_as_the_nearest_candidate_until_the_match_is_found(self):
        f = core_facts()
        f.materials = [m for m in f.materials if m.material_number not in ("M-A1", "M-A2")]  # only M-A3 (170 bar)
        self.assertEqual(evaluate(f).answer["decision"], "no_bid")  # the group counts as searched: rule 3 ...
        result = evaluate(f, allow_no_bid=False)  # ... unless the lead finds the search not conclusive yet
        self.assertEqual(result.answer["decision"], "bid")
        self.assertEqual(pos(result.answer, "1")["material_number"], "M-A3")
        self.assertIn("1", result.incomplete)
        self.assertTrue(any("nearest candidate M-A3" in i for i in result.issues))
        searches = [n for n in result.needed() if n["what"].startswith("search Kataloggruppe G-A")]
        self.assertEqual(searches, [])  # the group counts as searched (a candidate of it is known) ...
        self.assertEqual(pos(result.answer, "2")["material_number"], "M-B1")  # ... and the other position is exact

    def test_unknown_price_parts_count_as_zero_and_the_total_adds_up(self):
        f = core_facts()
        f.materials = [replace(m, conditions=[]) if m.material_number == "M-B1" else m for m in f.materials]
        answer = evaluate(f).answer
        total = sum(D(str(p["quantity"])) * D(str(p["unit_price"])) for p in answer["positions"])
        self.assertEqual(D(str(answer["total_net"])), total.quantize(D("0.01")))
        self.assertEqual(pos(answer, "2")["unit_price"], 0)
        self.assertGreater(pos(answer, "1")["unit_price"], 0)


if __name__ == "__main__":
    unittest.main()
