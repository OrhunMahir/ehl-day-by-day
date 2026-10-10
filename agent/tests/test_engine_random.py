"""Randomised scenarios against an independent reference.

A generator invents a tender (the ground truth), then writes it into facts the way messy sites present it: decoys
are added (superseded, look-alike, future-dated and wrong-sender records; inactive and near-miss materials; old
revisions and their amendments; withdrawn and superseded clarifications; expired and future price conditions), the
lists are shuffled, position numbers come in several styles and some keys come without a Q&A number. A deliberately
simple reference computes the expected answer from the ground truth alone, with Fractions instead of Decimal, so it
shares no code with the engine. Every answer field must agree. All data is invented.
"""
import math
import random
import unittest
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from fractions import Fraction as F

import test_engine_base  # noqa: F401  (puts agent/ on the path)

from engine import evaluate
from facts import (Assignment, AssignmentKind as K, Attr, BonusBand, ConditionGroupRule, DeliveryCommitment,
                   DiscountApproval, DiscountScope, EndReserve, Facts, FxRate, IndexWindow, Material, MetalIndex,
                   PackagingTariff, PriceCondition, ProjectBonus, QuantityChange, Requirement, Scale, SetupPool, Source,
                   SpecRevision, TechnicalAmendment, TenderPosition, Unit, Withdrawal)

SEEDS = range(300)
UTC = timezone.utc
SCEN = datetime(2030, 6, 15, 12, 0, tzinfo=UTC)
REF, LOOK = "T-RND-7", "T-RND-7-X"
DOMAIN, DIRECTOR = "bidder.test", "chef@bidder.test"


# ---------------------------------------------------------------------------------------------------------------------
# Ground truth


@dataclass
class TMat:
    number: str
    group: str
    bore: int
    stroke: int                 # nominal stroke, what the ERP shows
    pressure: int
    mounting: str
    cert: str                   # "" = none
    stock: int
    stock_lead: int
    prod_lead: int
    carton: int
    alloc_kg: D
    mass_kg: D
    index: str                  # "" = no metal index
    index_rate: D
    setup: D
    usd: bool
    price_unit: int
    scales: list                # [(min pieces, price per price_unit)]
    usable: bool = False        # the requirement states usable travel
    reserve: int = 0


@dataclass
class TPos:
    id: str
    mat: TMat
    is_set: bool
    pps: int                    # pieces per set of its requirement
    tender_qty: int             # in the position's unit
    stated: int                 # final quantity in the position's unit
    scope: DiscountScope | None = None
    packaging: bool = False
    pool: bool = False
    bonus: bool = False
    commit: tuple | None = None  # (material number, ceiling, weeks)

    @property
    def pieces(self):
        return self.stated * (self.pps if self.is_set else 1)


@dataclass
class World:
    positions: list
    mats: list
    fx: D
    approval: tuple | None      # (percent, min order value or None)
    scope_keys: dict            # key -> scope
    tariff: D
    pool_fee: D
    bands: list                 # [(lower, upper or None, percent)]
    no_bid: bool = False
    facts: Facts = None
    notes: list = field(default_factory=list)


def money(rng, lo, hi):
    return D(rng.randint(int(lo * 100), int(hi * 100))) / 100


def invent(seed: int) -> World:
    rng = random.Random(seed)
    n = rng.randint(1, 5)
    style = rng.choice(["num", "tens", "dotted", "alpha"])
    ids = [{"num": str(i + 1), "tens": str(10 * (i + 1)), "dotted": f"1.{i + 1}", "alpha": f"P-{i + 1:02d}"}[style]
           for i in range(n)]
    k = rng.randint(1, n)
    mats = []
    for i in range(k):
        price_unit = rng.choice([1, 1, 10, 100])
        mins = sorted({1, rng.randint(2, 15), rng.randint(16, 60)})
        price = money(rng, 20, 900) * price_unit
        scales = []
        for m in mins:
            scales.append((m, price))
            price = (price * D(rng.randint(85, 99)) / 100).quantize(D("0.01"))
        mats.append(TMat(
            number=f"M-{seed}-{i}", group=f"G-{i}", bore=rng.choice([32, 40, 50, 63, 80, 100]),
            stroke=200 + 60 * i + 10 * rng.randint(0, 4), pressure=rng.choice([160, 200, 210, 250]),
            mounting=rng.choice(["Flansch", "Schwenkauge", "Fußbefestigung", "Gewindezapfen"]),
            cert=rng.choice(["", "", "EN 1000", "EN 2000"]), stock=rng.randint(0, 60), stock_lead=rng.randint(1, 3),
            prod_lead=rng.randint(4, 12), carton=rng.randint(1, 12), alloc_kg=D(rng.randint(500, 5000)) / 1000,
            mass_kg=D(rng.randint(100, 2000)) / 1000, index=rng.choice(["", f"IX-{i}", f"IX-{i}"]),
            index_rate=money(rng, 1.5, 5), setup=rng.choice([D(0), money(rng, 10, 200)]), usd=rng.random() < 0.3,
            price_unit=price_unit, scales=scales, usable=rng.random() < 0.3, reserve=rng.randint(3, 15)))
    positions = []
    for i, pid in enumerate(ids):
        mat = mats[i] if i < k else rng.choice(mats)
        is_set = rng.random() < 0.25
        tender = rng.randint(1, 20) if is_set else rng.randint(1, 80)
        stated = rng.choice([tender, tender, rng.randint(1, 80)])
        positions.append(TPos(pid, mat, is_set, rng.randint(2, 6), tender, stated))
    # pieces per set belong to the requirement: positions of one material share them
    for p in positions:
        p.pps = next(q.pps for q in positions if q.mat is p.mat)
    scope_keys = {f"KG-{j}": rng.choice(list(DiscountScope)) for j in range(3)}
    for p in positions:
        if rng.random() < 0.4:
            p.scope = rng.choice(list(scope_keys))
        p.packaging = rng.random() < 0.5
        p.pool = rng.random() < 0.4
        p.bonus = rng.random() < 0.5
    pooled = {}
    for p in positions:
        pooled[p.mat.number] = pooled.get(p.mat.number, 0) + p.pieces
    for p in positions:
        if rng.random() < 0.3:
            number = p.mat.number if rng.random() < 0.7 else f"M-{seed}-other"
            ceiling = max(0, pooled[p.mat.number] + rng.choice([0, -1, 5]))
            p.commit = (number, ceiling, rng.randint(1, 3))
    t1 = money(rng, 200, 3000)
    bands = [(D(0), t1, D(0)), (t1, t1 + money(rng, 500, 5000), D(rng.choice([1, 2, 3]))),
             (None, None, D(rng.choice([4, 5])))]
    bands[2] = (bands[1][1], None, bands[2][2])
    world = World(positions, mats, fx=D(rng.randint(8000, 13000)) / 10000, approval=None, scope_keys=scope_keys,
                  tariff=money(rng, 1, 30), pool_fee=money(rng, 10, 900), bands=bands)
    if rng.random() < 0.85:
        percent = rng.choice([D(2), D("2.5"), D(3), D(5), D("7.5")])
        minimum = None
        if rng.random() < 0.5:
            value = reference_order_value(world)
            minimum = rng.choice([value, value + D("0.01"), value - D("0.01"), (value / 2).quantize(D("0.01")),
                                  value * 2])
        world.approval = (percent, minimum)
    world.no_bid = rng.random() < 0.1
    world.facts = render(world, rng)
    return world


# ---------------------------------------------------------------------------------------------------------------------
# Reference (Fractions, written from the rule text, no engine code)


def r2(x: F) -> F:
    return F(math.floor(x * 100 + F(1, 2)), 100)


def base_price(world: World, p: TPos) -> F:
    reached = [price for minimum, price in p.mat.scales if minimum <= p.pieces]
    per_piece = F(reached[-1]) / p.mat.price_unit
    return r2(per_piece * F(world.fx)) if p.mat.usd else per_piece


def reference_order_value(world: World) -> D:
    value = r2(sum((p.pieces * base_price(world, p) for p in world.positions), F(0)))
    return D(value.numerator) / D(value.denominator)


def largest_remainder(total: F, weights: dict, order: list) -> dict:
    cents = int(total * 100)
    whole = sum(weights.values(), F(0))
    exact = {k: cents * w / whole for k, w in weights.items()}
    got = {k: math.floor(v) for k, v in exact.items()}
    rest = cents - sum(got.values())
    for k in sorted(weights, key=lambda k: (-(exact[k] - got[k]), order.index(k)))[:rest]:
        got[k] += 1
    return {k: F(v, 100) for k, v in got.items()}


def reference(world: World) -> dict:
    percent = F(0)
    if world.approval:
        pct, minimum = world.approval
        if minimum is None or (not world.no_bid and reference_order_value(world) >= minimum):
            percent = F(pct)
    head = {"tender_reference": REF, "currency": "EUR", "discount_percent": percent}
    if world.no_bid:
        return {**head, "decision": "no_bid", "positions": [], "total_net": F(0)}
    order = [p.id for p in world.positions]
    base = {p.id: base_price(world, p) for p in world.positions}
    pool = {p.id: F(0) for p in world.positions}
    members = [p for p in world.positions if p.pool]
    if members:
        pool.update(largest_remainder(F(world.pool_fee), {p.id: p.pieces * F(p.mat.alloc_kg) for p in members}, order))
    bonus = {p.id: F(0) for p in world.positions}
    members = [p for p in world.positions if p.bonus]
    if members:
        values = {p.id: p.pieces * base[p.id] for p in members}
        total = sum(values.values(), F(0))
        credit = F(0)
        for lower, upper, pct in world.bands:
            top = total if upper is None else min(total, F(upper))
            credit += max(F(0), top - F(lower)) * F(pct) / 100
        credit = r2(credit)
        if credit:
            bonus.update(largest_remainder(credit, values, order))
    pooled = {}
    for p in world.positions:
        pooled[p.mat.number] = pooled.get(p.mat.number, 0) + p.pieces
    keep = 1 - percent / 100
    out, total_net = [], F(0)
    for p in world.positions:
        m, q = p.mat, p.pieces
        mass = F(m.mass_kg) * F(m.index_rate) if m.index else F(0)
        packaging = math.ceil(F(q, m.carton)) * F(world.tariff) if p.packaging else F(0)
        scope = world.scope_keys[p.scope] if p.scope else DiscountScope.BASE
        b = base[p.id] * keep if scope in (DiscountScope.BASE, DiscountScope.BASE_AND_SURCHARGE) else base[p.id]
        s = mass * keep if scope == DiscountScope.BASE_AND_SURCHARGE else mass
        unit = r2(b + s + (F(m.setup) + pool[p.id] + packaging - bonus[p.id]) / q)
        if p.commit and p.commit[0] == m.number and pooled[m.number] <= p.commit[1]:
            lead = p.commit[2]
        else:
            lead = m.stock_lead if m.stock >= pooled[m.number] else m.prod_lead
        total_net += q * unit
        out.append({"position": p.id, "material_number": m.number, "quantity": q, "unit_price": unit,
                    "lead_time_weeks": lead, "base_unit_price": r2(base[p.id]),
                    "surcharge_per_unit": r2(mass + (F(m.setup) + pool[p.id]) / q),
                    "packaging_per_unit": r2(packaging / q), "rebate_total": bonus[p.id]})
    return {**head, "decision": "bid", "positions": out, "total_net": total_net}


# ---------------------------------------------------------------------------------------------------------------------
# Rendering the truth into facts, with decoys


def render(w: World, rng: random.Random) -> Facts:
    clock = [datetime(2030, 3, 1, 8, 0, tzinfo=UTC)]

    def qa_time():
        clock[0] += timedelta(hours=rng.randint(1, 60))
        return clock[0]

    def true_time():          # a record date not after the scenario date (sometimes the same morning)
        return rng.choice([SCEN - timedelta(hours=rng.randint(1, 3)), SCEN - timedelta(days=rng.randint(1, 30))])

    def src(when, local="", domain=DOMAIN):
        return Source(date=when, sender=f"{local}@{domain}" if local else "")

    def keyed(make, local, key, true_value, other_value):
        """The authorized record plus decoys: superseded older, look-alike newer, future, wrong sender/domain."""
        t = true_time()
        return [make(REF, key, true_value, src(t, local)),
                make(REF, key, other_value, src(t - timedelta(days=rng.randint(1, 9)), local)),
                make(LOOK, key, other_value, src(SCEN - timedelta(minutes=30), local)),
                make(REF, key, other_value, src(SCEN + timedelta(minutes=rng.choice([1, 60 * 24])), local)),
                make(REF, key, other_value, src(SCEN - timedelta(minutes=10), "vertrieb")),
                make(REF, key, other_value, src(SCEN - timedelta(minutes=5), local, "other.test"))]

    f = Facts(tender_reference=REF, scenario_date=SCEN, bidder_domain=DOMAIN, director_address=DIRECTOR)
    numbers = iter(range(1, 1000))

    # revisions, requirements, amendments, end reserves
    cur, old = f"REV-{rng.randint(100, 999)}", f"REV-{rng.randint(1000, 1999)}"
    cur_reqs, old_reqs = [], []
    for m in w.mats:
        p_set = next(p for p in w.positions if p.mat is m)
        stroke = m.stroke - 2 * m.reserve if m.usable else m.stroke
        req = Requirement(f"A{m.group}", D(m.bore), D(stroke), D(m.pressure), m.mounting,
                          m.cert or rng.choice(["keine gefordert", "", None]), catalogue_group=m.group,
                          pieces_per_set=D(p_set.pps), usable_travel=m.usable, end_reserve_key=f"E-{m.group}")
        if rng.random() < 0.5:        # the revision states another value; the latest amendment restores the truth
            attr, wrong, right = ((Attr.STROKE, req.stroke_mm + 20, req.stroke_mm) if rng.random() < 0.5
                                  else (Attr.PRESSURE, D(m.pressure + 5), D(m.pressure)))
            req = Requirement(**{**req.__dict__, attr.value: wrong})
            f.amendments += [
                TechnicalAmendment(cur, req.requirement, attr, str(wrong + 1), f"F-{next(numbers)}", src(qa_time())),
                TechnicalAmendment(cur, req.requirement, attr, str(right), f"F-{next(numbers)}", src(qa_time())),
                TechnicalAmendment(old, req.requirement, attr, str(wrong + 2), f"F-{next(numbers)}", src(qa_time()))]
        cur_reqs.append(req)
        old_reqs.append(Requirement(**{**req.__dict__, "stroke_mm": req.stroke_mm + 10}))
        if m.usable:
            f.end_reserves += keyed(lambda r, k, v, s: EndReserve(r, k, cur, D(v), s), "technik", f"E-{m.group}",
                                    m.reserve, m.reserve + 2)
            f.end_reserves.append(EndReserve(REF, f"E-{m.group}", old, D(m.reserve + 5), src(true_time(), "technik")))
    f.revisions = [SpecRevision(cur, True, date(2030, 2, rng.randint(1, 28)), cur_reqs),
                   SpecRevision(old, rng.choice([False, None]), date(2030, rng.choice([1, 3]), 10), old_reqs)]

    # positions and clarifications
    for p in w.positions:
        f.positions.append(TenderPosition(p.id, D(p.tender_qty), Unit.SET if p.is_set else Unit.PIECE,
                                          f"A{p.mat.group}"))
        wrong = p.stated + rng.randint(1, 9)
        story = rng.choice(["none", "change", "two", "withdrawn_later", "withdrawn_only"])
        if p.stated != p.tender_qty and story in ("none", "withdrawn_only"):
            story = "change"
        if story in ("change", "withdrawn_later"):
            f.quantity_changes.append(QuantityChange(p.id, D(p.stated), f"F-{next(numbers)}", src(qa_time())))
        if story == "two":
            f.quantity_changes.append(QuantityChange(p.id, D(wrong), f"F-{next(numbers)}", src(qa_time())))
            f.quantity_changes.append(QuantityChange(p.id, D(p.stated), f"F-{next(numbers)}", src(qa_time())))
        if story in ("withdrawn_later", "withdrawn_only"):
            n = f"F-{next(numbers)}"
            f.quantity_changes.append(QuantityChange(p.id, D(wrong), n, src(qa_time())))
            f.withdrawals.append(Withdrawal(f"F-{next(numbers)}", n, src(qa_time())))

    # materials with decoys and price conditions
    removed = rng.choice(w.mats).number if w.no_bid else None
    for m in w.mats:
        valid = PriceCondition(datetime(2030, 1, 1, tzinfo=UTC), datetime(2030, 12, 31, 23, 59, 59, tzinfo=UTC),
                               "USD" if m.usd else "EUR", "FX-1" if m.usd else "", D(m.price_unit),
                               [Scale(D(q), price) for q, price in m.scales])
        expired = PriceCondition(date(2029, 1, 1), date(2029, 12, 31), "EUR", "", D(1), [Scale(D(1), D("1.11"))])
        future = PriceCondition(SCEN + timedelta(hours=1), None, "EUR", "", D(1), [Scale(D(1), D("2.22"))])
        true = Material(m.number, True, m.group, D(m.bore), D(m.stroke), D(m.pressure),
                        rng.choice([m.mounting, m.mounting.lower(), m.mounting.upper()]),
                        m.cert or rng.choice(["", None, "keine"]), D(m.stock), m.stock_lead, m.prod_lead,
                        D(m.carton), m.alloc_kg, m.mass_kg, m.index, m.setup, rng.sample([valid, expired, future], 3))
        near = rng.choice(["bore_mm", "pressure_bar", "stroke_mm"])
        decoys = [Material(**{**true.__dict__, "material_number": m.number + "-I", "active": False}),
                  Material(**{**true.__dict__, "material_number": m.number + "-N",
                              near: getattr(true, near) + rng.choice([1, 2, 3, 4])})]
        if m.usable:     # the trap: a material whose stroke is the usable travel without reserves
            decoys.append(Material(**{**true.__dict__, "material_number": m.number + "-U",
                                      "stroke_mm": D(m.stroke - 2 * m.reserve)}))
        f.materials += decoys + ([] if m.number == removed else [true])
        if m.index:
            day = SCEN.date()
            start = rng.choice([day, day - timedelta(days=rng.randint(1, 40))])
            end = rng.choice([day, day + timedelta(days=rng.randint(1, 40))])
            windows = [IndexWindow(m.index_rate + 1, None, start - timedelta(days=1)),
                       IndexWindow(m.index_rate, start, end), IndexWindow(m.index_rate + 2, end + timedelta(days=1))]
            rng.shuffle(windows)
            f.metal_indices += keyed(lambda r, k, v, s: MetalIndex(r, k, v, s), "kalkulation", m.index, windows,
                                     [IndexWindow(m.index_rate + 3)])
    f.searched_groups = [m.group for m in w.mats]
    f.fx_rates = keyed(lambda r, k, v, s: FxRate(r, k, "USD", v, s), "finanzen", "FX-1", w.fx, w.fx + D("0.1"))

    # keys and their assignments (some without a Q&A number, as if read from another site)
    def assign(p, kind, key):
        number = rng.choice([f"F-{next(numbers)}", ""])
        if rng.random() < 0.3:      # an older assignment to another key, replaced by the true one
            f.assignments.append(Assignment(p.id, kind, key + "-OLD", "", src(qa_time())))
        f.assignments.append(Assignment(p.id, kind, key, number, src(qa_time())))

    for key, scope in w.scope_keys.items():
        other = rng.choice([s for s in DiscountScope if s != scope])
        f.condition_groups += keyed(lambda r, k, v, s: ConditionGroupRule(r, k, v, s), "kalkulation", key, scope, other)
    f.packaging_tariffs = keyed(lambda r, k, v, s: PackagingTariff(r, k, v, s), "logistik", "VP-1", w.tariff,
                                w.tariff + 7)
    f.packaging_tariffs.append(PackagingTariff(REF, "VP-1-OLD", D(99), src(true_time(), "logistik")))
    f.setup_pools = keyed(lambda r, k, v, s: SetupPool(r, k, v, s), "kalkulation", "RP-1", w.pool_fee,
                          w.pool_fee + 50)
    bands = [BonusBand(lo, hi, pct) for lo, hi, pct in w.bands]
    f.project_bonuses = keyed(lambda r, k, v, s: ProjectBonus(r, k, v, s), "finanzen", "PB-1", bands,
                              [BonusBand(D(0), None, D(20))])
    for p in w.positions:
        if p.scope:
            assign(p, K.CONDITION_GROUP, p.scope)
        if p.packaging:
            assign(p, K.PACKAGING, "VP-1")
        if p.pool:
            assign(p, K.SETUP_POOL, "RP-1")
        if p.bonus:
            assign(p, K.PROJECT_BONUS, "PB-1")
        if p.commit:
            key = f"TZ-{p.id}"
            number, ceiling, weeks = p.commit
            f.delivery_commitments += keyed(
                lambda r, k, v, s: DeliveryCommitment(r, k, number, D(ceiling), v, s), "logistik", key, weeks, 0)
            assign(p, K.DELIVERY, key)

    if w.approval:
        pct, minimum = w.approval
        t = true_time()
        f.discount_approvals = [
            DiscountApproval(REF, pct, minimum, src(t, "chef")),
            DiscountApproval(REF, D(1), None, src(t - timedelta(days=2), "chef")),
            DiscountApproval(LOOK, D(9), None, src(SCEN - timedelta(minutes=20), "chef")),
            DiscountApproval(REF, D(9), None, src(SCEN + timedelta(minutes=1), "chef")),
            DiscountApproval(REF, D(9), None, src(SCEN - timedelta(minutes=10), "vertrieb"))]

    for items in (f.positions, f.quantity_changes, f.withdrawals, f.amendments, f.assignments, f.materials,
                  f.discount_approvals, f.condition_groups, f.metal_indices, f.packaging_tariffs,
                  f.delivery_commitments, f.fx_rates, f.setup_pools, f.project_bonuses, f.end_reserves, f.revisions):
        rng.shuffle(items)
    return f


# ---------------------------------------------------------------------------------------------------------------------


def as_dec(x) -> D:
    if isinstance(x, F):
        return D(x.numerator) / D(x.denominator)
    return D(str(x))


class RandomScenarios(unittest.TestCase):
    def test_engine_matches_reference(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                world = invent(seed)
                result = evaluate(world.facts)
                want, got = reference(world), result.answer
                self.assertEqual(result.incomplete, [])
                self.assertEqual(got["decision"], want["decision"])
                for name in ("tender_reference", "currency"):
                    self.assertEqual(got[name], want[name])
                for name in ("discount_percent", "total_net"):
                    self.assertEqual(as_dec(got[name]), as_dec(want[name]), name)
                self.assertEqual([p["position"] for p in got["positions"]],
                                 [p["position"] for p in want["positions"]])
                for g, e in zip(got["positions"], want["positions"]):
                    self.assertEqual(set(g), set(e), g["position"])
                    for name in e:
                        if isinstance(e[name], str):
                            self.assertEqual(g[name], e[name], (g["position"], name))
                        else:
                            self.assertEqual(as_dec(g[name]), as_dec(e[name]), (g["position"], name))


if __name__ == "__main__":
    unittest.main()
