"""Advanced pricing layers and the authority rules (12, 14, 18, 20-28) on invented data."""
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

from test_engine_base import DIRECTOR, DOMAIN, REF, at, condition, core_facts, mail, material

from engine import build_bid, evaluate
from facts import (Assignment, AssignmentKind as K, Attr, BonusBand, ConditionGroupRule, DeliveryCommitment,
                   DiscountApproval, DiscountScope, EndReserve, Facts, FxRate, IndexWindow, MetalIndex,
                   PackagingTariff, ProjectBonus, Requirement, SetupPool, SpecRevision, TechnicalAmendment,
                   TenderPosition, Unit, Withdrawal)

SCEN = core_facts().scenario_date


def one_position(**material_kw) -> Facts:
    """One position "1" of 10 pieces, material M-1 at 100.00 EUR, 10 % unconditional discount."""
    material_kw.setdefault("conditions", [condition((1, "100.00"))])
    return Facts(
        tender_reference=REF, scenario_date=SCEN, bidder_domain=DOMAIN, director_address=DIRECTOR,
        positions=[TenderPosition("1", D(10), Unit.PIECE, "A")],
        revisions=[SpecRevision("R-1", True, date(2030, 1, 1), [Requirement("A", D(1), D(2), D(3), "m")])],
        materials=[material("M-1", 1, 2, 3, "m", None, stock=D(100), stock_lead_weeks=1, production_lead_weeks=9,
                            **material_kw)],
        discount_approvals=[DiscountApproval(REF, D(10), source=at("2030-03-01", DIRECTOR))],
    )


def assign(f: Facts, kind, key, position="1", qa="Q9", day="2030-02-01"):
    f.assignments.append(Assignment(position, kind, key, qa, at(day)))


def index(key="IX-1", day="2030-03-01", ref=REF, windows=None, sender="kalkulation"):
    windows = windows or [IndexWindow(D("3.50"))]
    return MetalIndex(ref, key, windows, mail(sender, day))


class Clauses(unittest.TestCase):
    """Rule 12. Base 100.00, mass surcharge 2 kg x 3.50 = 7.00, setup 50.00 / 10 = 5.00 (never discounted), 10 %."""

    def facts(self):
        f = one_position(surcharge_mass_kg=D(2), metal_index="IX-1", setup_fee_eur=D(50))
        f.metal_indices = [index()]
        return f

    def unit(self, scope):
        f = self.facts()
        if scope is not None:
            f.condition_groups = [ConditionGroupRule(REF, "KG-1", scope, mail("kalkulation", "2030-03-01"))]
            assign(f, K.CONDITION_GROUP, "KG-1")
        return build_bid(f)["positions"][0]

    def test_default_is_base_only(self):
        self.assertEqual(self.unit(None)["unit_price"], 102)          # 90 + 7 + 5

    def test_base(self):
        self.assertEqual(self.unit(DiscountScope.BASE)["unit_price"], 102)

    def test_base_and_surcharge(self):
        self.assertEqual(self.unit(DiscountScope.BASE_AND_SURCHARGE)["unit_price"], 101.3)   # 90 + 6.30 + 5

    def test_none(self):
        self.assertEqual(self.unit(DiscountScope.NONE)["unit_price"], 112)            # 100 + 7 + 5

    def test_surcharge_reported_before_discount(self):                                  # rules 12, 18, 25
        p = self.unit(DiscountScope.BASE_AND_SURCHARGE)
        self.assertEqual((p["surcharge_per_unit"], p["base_unit_price"]), (12, 100))

    def test_group_overrides_per_position_with_same_material(self):
        f = self.facts()
        f.positions.append(TenderPosition("2", D(10), Unit.PIECE, "A"))
        f.condition_groups = [ConditionGroupRule(REF, "KG-1", DiscountScope.NONE, mail("kalkulation", "2030-03-01"))]
        assign(f, K.CONDITION_GROUP, "KG-1", position="2")
        prices = [p["unit_price"] for p in build_bid(f)["positions"]]
        self.assertEqual(prices, [102, 112])

    def test_rule_from_wrong_sender_ignored(self):                                      # rule 21
        f = self.facts()
        f.condition_groups = [ConditionGroupRule(REF, "KG-1", DiscountScope.NONE, mail("vertrieb", "2030-03-01"))]
        assign(f, K.CONDITION_GROUP, "KG-1")
        self.assertEqual(build_bid(f)["positions"][0]["unit_price"], 102)


class Currency(unittest.TestCase):
    """Rule 22. USD 123.45 per 10 pieces -> 12.345 USD; 1.1 EUR/USD -> 13.5795 -> 13.58 EUR, then 10 % discount."""

    def facts(self):
        f = one_position(conditions=[condition((1, "123.45"), unit=10, currency="USD", fx_key="FX-1")])
        f.fx_rates = [
            FxRate(REF, "FX-1", "USD", D("1.1"), mail("finanzen", "2030-03-01")),
            FxRate(REF, "FX-1", "USD", D("2.0"), mail("finanzen", "2030-03-20")),         # future
            FxRate(REF + "-B", "FX-1", "USD", D("3.0"), mail("finanzen", "2030-03-10")),  # other tender
            FxRate(REF, "FX-1", "USD", D("4.0"), mail("vertrieb", "2030-03-10")),         # not authorized
            FxRate(REF, "FX-1", "USD", D("5.0"), at("2030-03-10", "finanzen@other.test")),  # other domain
        ]
        return f

    def test_converted_and_rounded_base(self):
        p = build_bid(self.facts())["positions"][0]
        self.assertEqual(p["base_unit_price"], 13.58)
        self.assertEqual(p["unit_price"], 12.22)      # 13.58 x 0.9 = 12.222

    def test_missing_rate_reported(self):
        f = self.facts()
        f.fx_rates = []
        result = evaluate(f)
        self.assertEqual(result.incomplete, ["1"])
        self.assertTrue(any("FX key" in i for i in result.issues))

    def test_converted_base_used_for_threshold(self):                                  # rules 16, 22
        f = self.facts()
        f.discount_approvals = [DiscountApproval(REF, D(10), D("135.80"), at("2030-03-01", DIRECTOR))]
        self.assertEqual(build_bid(f)["discount_percent"], 10)
        f.discount_approvals = [DiscountApproval(REF, D(10), D("135.81"), at("2030-03-01", DIRECTOR))]
        self.assertEqual(build_bid(f)["discount_percent"], 0)


class MetalIndices(unittest.TestCase):
    """Rule 28: the window containing the scenario date (2030-03-15), inclusive, from the latest exact record."""

    def surcharge(self, indices):
        f = one_position(surcharge_mass_kg=D("0.5"), metal_index="IX-1")
        f.metal_indices = indices
        return build_bid(f)["positions"][0]["surcharge_per_unit"]

    def windows(self):
        return [IndexWindow(D("2.00"), date(2030, 1, 1), date(2030, 3, 14)),
                IndexWindow(D("3.00"), date(2030, 3, 15), date(2030, 4, 30)),
                IndexWindow(D("4.00"), date(2030, 5, 1), None)]

    def test_window_start_is_inclusive(self):
        self.assertEqual(self.surcharge([index(windows=self.windows())]), 1.5)

    def test_window_end_is_inclusive(self):
        w = [IndexWindow(D("6.00"), date(2030, 2, 1), date(2030, 3, 15)), IndexWindow(D("8.00"), date(2030, 3, 16))]
        self.assertEqual(self.surcharge([index(windows=w)]), 3)

    def test_newer_exact_record_replaces_older_and_look_alike_ignored(self):
        older = index(day="2030-02-01", windows=[IndexWindow(D("9.00"))])
        look_alike = index(day="2030-03-10", ref=REF + "-B", windows=[IndexWindow(D("7.00"))])
        self.assertEqual(self.surcharge([older, index(windows=self.windows()), look_alike]), 1.5)

    def test_material_without_index_has_zero_surcharge(self):
        f = one_position()
        self.assertEqual(build_bid(f)["positions"][0]["surcharge_per_unit"], 0)


class Packaging(unittest.TestCase):
    """Rule 23. 10 pieces, 3 per carton -> 4 cartons x 12.50 = 50.00 -> 5.00 per piece, never discounted."""

    def facts(self):
        f = one_position(pieces_per_carton=D(3))
        f.packaging_tariffs = [PackagingTariff(REF, "VP-1", D("12.50"), mail("logistik", "2030-03-01")),
                               PackagingTariff(REF + "-B", "VP-1", D("99"), mail("logistik", "2030-03-05"))]
        assign(f, K.PACKAGING, "VP-1")
        return f

    def test_cartons_rounded_up_per_position(self):
        p = build_bid(self.facts())["positions"][0]
        self.assertEqual((p["packaging_per_unit"], p["unit_price"], p["quantity"]), (5, 95, 10))

    def test_unassigned_position_has_zero_packaging(self):
        f = self.facts()
        f.assignments = []
        self.assertEqual(build_bid(f)["positions"][0]["packaging_per_unit"], 0)

    def test_unknown_carton_size_leaves_field_blank(self):
        f = self.facts()
        f.materials[0] = replace(f.materials[0], pieces_per_carton=None)
        result = evaluate(f)
        self.assertNotIn("packaging_per_unit", result.answer["positions"][0])
        self.assertEqual(result.incomplete, ["1"])


class SetupPools(unittest.TestCase):
    """Rule 25. Pool fee 10.00 over positions 2, 9, 10 with equal weight 3 x 1 kg: 3.33 each, the left cent to "2"."""

    def facts(self):
        f = one_position(allocation_mass_kg=D(1))
        f.discount_approvals = []
        f.positions = [TenderPosition(n, D(3), Unit.PIECE, "A") for n in ("10", "9", "2")]
        f.setup_pools = [SetupPool(REF, "RP-1", D("10.00"), mail("kalkulation", "2030-03-01"))]
        for n in ("10", "9", "2"):
            assign(f, K.SETUP_POOL, "RP-1", position=n)
        return f

    def test_allocation_into_surcharge(self):
        positions = {p["position"]: p for p in build_bid(self.facts())["positions"]}
        self.assertEqual(positions["2"]["surcharge_per_unit"], 1.11)       # 3.34 / 3 = 1.1133
        self.assertEqual(positions["9"]["surcharge_per_unit"], 1.11)       # 3.33 / 3 = 1.11
        self.assertEqual(positions["2"]["unit_price"], 101.11)
        self.assertEqual(positions["9"]["unit_price"], 101.11)

    def test_pool_and_ordinary_setup_add_up(self):
        f = self.facts()
        f.materials[0] = replace(f.materials[0], setup_fee_eur=D("3.00"))
        positions = {p["position"]: p for p in build_bid(f)["positions"]}
        self.assertEqual(positions["2"]["surcharge_per_unit"], 2.11)       # (3.00 + 3.34) / 3 = 2.1133

    def test_weight_is_quantity_times_allocation_mass(self):
        f = self.facts()
        f.positions = [TenderPosition("1", D(1), Unit.PIECE, "A"), TenderPosition("2", D(3), Unit.PIECE, "A")]
        f.assignments = []
        assign(f, K.SETUP_POOL, "RP-1", position="1")
        assign(f, K.SETUP_POOL, "RP-1", position="2")
        lines = {ln.position: ln for ln in evaluate(f).lines}
        self.assertEqual((lines["1"].pool, lines["2"].pool), (D("2.50"), D("7.50")))

    def test_pool_never_discounted(self):
        f = self.facts()
        f.discount_approvals = [DiscountApproval(REF, D(50), source=at("2030-03-01", DIRECTOR))]
        positions = {p["position"]: p for p in build_bid(f)["positions"]}
        self.assertEqual(positions["2"]["unit_price"], 51.11)              # 50 + 3.34 / 3


class ProjectBonusTest(unittest.TestCase):
    """Rule 26. Bands 0-1000 0 %, 1000-2000 2 %, from 2000 5 %. Members 1 (15 x 100) and 2 (10 x 100): 2500.00
    -> 20.00 + 25.00 = 45.00, split 1500:1000 -> 27.00 and 18.00. Position 3 is not a member."""

    def facts(self):
        f = one_position()
        f.positions = [TenderPosition("1", D(15), Unit.PIECE, "A"), TenderPosition("2", D(10), Unit.PIECE, "A"),
                       TenderPosition("3", D(5), Unit.PIECE, "A")]
        f.materials[0] = replace(f.materials[0], stock=D(1000))
        bands = [BonusBand(D(0), D(1000), D(0)), BonusBand(D(1000), D(2000), D(2)), BonusBand(D(2000), None, D(5))]
        f.project_bonuses = [ProjectBonus(REF, "PB-1", bands, mail("finanzen", "2030-03-01"))]
        assign(f, K.PROJECT_BONUS, "PB-1", position="1")
        assign(f, K.PROJECT_BONUS, "PB-1", position="2")
        return f

    def test_marginal_bands_and_allocation(self):
        positions = {p["position"]: p for p in build_bid(self.facts())["positions"]}
        self.assertEqual([positions[n]["rebate_total"] for n in "123"], [27, 18, 0])

    def test_bonus_subtracted_per_piece_and_not_discounted(self):
        positions = {p["position"]: p for p in build_bid(self.facts())["positions"]}
        self.assertEqual(positions["1"]["unit_price"], 88.2)    # 90 - 27 / 15
        self.assertEqual(positions["2"]["unit_price"], 88.2)    # 90 - 18 / 10
        self.assertEqual(positions["3"]["unit_price"], 90)

    def test_bonus_on_undiscounted_converted_base(self):
        f = self.facts()
        f.discount_approvals = [DiscountApproval(REF, D(50), source=at("2030-03-01", DIRECTOR))]
        positions = {p["position"]: p for p in build_bid(f)["positions"]}
        self.assertEqual(positions["1"]["rebate_total"], 27)


class Delivery(unittest.TestCase):
    """Rule 24. Commitment C-1: M-1 within 12 pieces in 3 weeks; stock 100 -> 1 week otherwise."""

    def facts(self, ceiling=12, material_number="M-1", qty=12):
        f = one_position()
        f.positions[0] = TenderPosition("1", D(qty), Unit.PIECE, "A")
        f.delivery_commitments = [DeliveryCommitment(REF, "C-1", material_number, D(ceiling), 3,
                                                     mail("logistik", "2030-03-01"))]
        assign(f, K.DELIVERY, "C-1")
        return f

    def lead(self, f):
        return build_bid(f)["positions"][0]["lead_time_weeks"]

    def test_within_inclusive_ceiling(self):
        self.assertEqual(self.lead(self.facts()), 3)

    def test_above_ceiling_uses_stock_rule(self):
        self.assertEqual(self.lead(self.facts(ceiling=11)), 1)

    def test_other_material_uses_stock_rule(self):
        self.assertEqual(self.lead(self.facts(material_number="M-9")), 1)

    def test_ceiling_on_pooled_demand(self):
        f = self.facts()
        f.positions.append(TenderPosition("2", D(1), Unit.PIECE, "A"))
        self.assertEqual(self.lead(f), 1)

    def test_newer_failed_record_does_not_revive_older(self):                          # rule 21
        f = self.facts(ceiling=11)
        f.delivery_commitments.insert(0, DeliveryCommitment(REF, "C-1", "M-1", D(50), 2,
                                                            mail("logistik", "2030-02-01")))
        self.assertEqual(self.lead(f), 1)


class UsableTravel(unittest.TestCase):
    """Rules 14 and 20. Requirement A in R-2: usable travel 280, reserve key E-1. Q&A amends it to 290 for R-2 (and to
    999 for R-1, which does not apply). Reserve for E-1 in R-2: 5 mm per end -> nominal stroke 300."""

    def facts(self):
        f = core_facts()
        f.revisions[1].requirements[0] = replace(f.revisions[1].requirements[0], stroke_mm=D(280),
                                                 usable_travel=True, end_reserve_key="E-1")
        f.amendments = [
            TechnicalAmendment("R-2", "A", Attr.STROKE, "285", "Q6", at("2030-02-02")),
            TechnicalAmendment("R-2", "A", Attr.STROKE, "290", "Q7", at("2030-02-03")),
            TechnicalAmendment("R-1", "A", Attr.STROKE, "999", "Q8", at("2030-02-04")),
        ]
        f.end_reserves = [EndReserve(REF, "E-1", "R-2", D(5), mail("technik", "2030-03-01")),
                          EndReserve(REF, "E-1", "R-1", D(50), mail("technik", "2030-03-02"))]
        return f

    def test_amended_usable_travel_plus_two_reserves(self):
        self.assertEqual(build_bid(self.facts())["positions"][0]["material_number"], "M-A1")

    def test_missing_reserve_is_reported_not_no_bid(self):
        f = self.facts()
        f.end_reserves = f.end_reserves[1:]
        result = evaluate(f)
        self.assertEqual(result.answer["decision"], "bid")
        self.assertTrue(any("end reserve" in i for i in result.issues))

    def test_amendment_changes_only_named_attribute(self):                             # rule 14
        f = core_facts()
        f.amendments = [TechnicalAmendment("R-2", "A", Attr.PRESSURE, "170", "Q6", at("2030-02-02"))]
        self.assertEqual(build_bid(f)["positions"][0]["material_number"], "M-A3")

    def test_withdrawn_amendment_ignored(self):                                         # rule 13
        f = core_facts()
        f.amendments = [TechnicalAmendment("R-2", "A", Attr.PRESSURE, "170", "Q6", at("2030-02-02"))]
        f.withdrawals.append(Withdrawal("Q10", "Q6", at("2030-02-12")))
        self.assertEqual(build_bid(f)["positions"][0]["material_number"], "M-A1")


class FinalPrice(unittest.TestCase):
    """Rule 27, all layers at once on one position of 7 pieces, 10 % discount, scope base + surcharge:
    base 100.00; mass 0.3 kg x 3.50 = 1.05; setup 20.00; pool 10.00 (only member); 3 per carton -> 3 cartons x 4.00
    = 12.00; bonus band 5 % from 0 -> 700 x 5 % = 35.00.
    90.00 + 0.945 + (20 + 10 + 12 - 35) / 7 = 90.945 + 1.0 = 91.945 -> 91.95."""

    def test_combined(self):
        f = one_position(surcharge_mass_kg=D("0.3"), metal_index="IX-1", setup_fee_eur=D(20),
                         allocation_mass_kg=D(1), pieces_per_carton=D(3))
        f.positions[0] = TenderPosition("1", D(7), Unit.PIECE, "A")
        f.metal_indices = [index()]
        f.condition_groups = [ConditionGroupRule(REF, "KG", DiscountScope.BASE_AND_SURCHARGE,
                                                 mail("kalkulation", "2030-03-01"))]
        f.setup_pools = [SetupPool(REF, "RP", D(10), mail("kalkulation", "2030-03-01"))]
        f.packaging_tariffs = [PackagingTariff(REF, "VP", D(4), mail("logistik", "2030-03-01"))]
        f.project_bonuses = [ProjectBonus(REF, "PB", [BonusBand(D(0), None, D(5))], mail("finanzen", "2030-03-01"))]
        for kind, key in ((K.CONDITION_GROUP, "KG"), (K.SETUP_POOL, "RP"), (K.PACKAGING, "VP"),
                          (K.PROJECT_BONUS, "PB")):
            assign(f, kind, key)
        p = build_bid(f)["positions"][0]
        self.assertEqual(p, {"position": "1", "material_number": "M-1", "quantity": 7, "unit_price": 91.95,
                             "lead_time_weeks": 1, "base_unit_price": 100, "surcharge_per_unit": 5.34,
                             "packaging_per_unit": 1.71, "rebate_total": 35})
        # surcharge 1.05 + 30 / 7 = 5.3357 -> 5.34; packaging 12 / 7 = 1.714 -> 1.71


if __name__ == "__main__":
    unittest.main()
