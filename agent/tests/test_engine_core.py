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
        f.quantity_changes = [QuantityChange("Q2", "1", D(20), at("2030-02-05"))]
        self.assertEqual(pos(build_bid(f), "1")["quantity"], 10)

    def test_set_converted_with_governing_spec(self):                  # rule 17
        self.assertEqual(pos(build_bid(core_facts()), "2")["quantity"], 12)

    def test_set_change_in_q_and_a_is_in_sets(self):                   # rule 17
        f = core_facts()
        f.quantity_changes.append(QuantityChange("Q4", "2", D(5), at("2030-02-20")))
        self.assertEqual(pos(build_bid(f), "2")["quantity"], 20)

    def test_set_without_pieces_per_set_leaves_position_out(self):
        f = core_facts()
        f.revisions[1].requirements[1] = replace(f.revisions[1].requirements[1], pieces_per_set=None)
        result = evaluate(f)
        self.assertEqual([p["position"] for p in result.answer["positions"]], ["1"])
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
        answer = build_bid(f)
        self.assertEqual((answer["decision"], answer["positions"], answer["total_net"]), ("no_bid", [], 0))

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

    def test_incomplete_requirement_is_not_a_no_bid(self):
        f = core_facts()
        f.revisions[1].requirements[0] = replace(f.revisions[1].requirements[0], bore_mm=None)
        result = evaluate(f)
        self.assertEqual(result.answer["decision"], "bid")
        self.assertEqual([p["position"] for p in result.answer["positions"]], ["2"])

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
        f.quantity_changes.append(QuantityChange("Q5", "1", D(50), at("2030-02-25")))
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


if __name__ == "__main__":
    unittest.main()
