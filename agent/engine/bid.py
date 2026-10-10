"""From facts to the answer object. Pure and deterministic: the same facts always give the same bid."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from facts import AssignmentKind, DiscountScope, Facts, Material, TenderPosition, norm_key

from . import resolve
from .money import allocate_cents, ceil_int, div, half_up, position_key
from .pricing import base_unit_price, bonus_credit, index_rate

ZERO = Decimal(0)


@dataclass
class Line:
    """Working record for one position; money fields are unrounded EUR, None when unknown."""
    position: str
    material: Material | None = None
    pieces: int | None = None
    base: Decimal | None = None             # per piece, before discount
    mass_surcharge: Decimal | None = None   # per piece, before discount
    setup: Decimal | None = None            # ordinary setup fee, whole position
    pool: Decimal | None = ZERO             # allocated shared setup, whole position
    packaging: Decimal | None = ZERO        # carton cost, whole position
    bonus: Decimal | None = ZERO            # allocated project bonus, whole position
    scope: DiscountScope = DiscountScope.BASE
    lead_weeks: int | None = None
    unit_price: Decimal | None = None


@dataclass
class BidResult:
    answer: dict
    issues: list[str] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)   # positions whose unit price could not be fully computed
    omitted: list[str] = field(default_factory=list)      # tender positions not in the answer; mark them in the
                                                          # form as "Position auslassen" (rule 9)


def build_bid(facts: Facts) -> dict:
    """The answer object for the run's answer schema."""
    return evaluate(facts).answer


def evaluate(facts: Facts) -> BidResult:
    """The answer object plus what could not be decided (`issues`) and the per-position working (`lines`)."""
    issues: list[str] = []
    revision = resolve.governing_revision(facts, issues)
    positions = sorted(facts.positions, key=lambda p: position_key(p.position))

    lines: list[Line] = []
    no_match: list[str] = []
    for pos in positions:
        line = Line(pos.position)
        req = resolve.requirement(facts, revision, pos.requirement, issues)
        line.pieces = resolve.final_pieces(facts, pos, req, issues)
        if req is not None and resolve.requirement_complete(req):
            line.material = resolve.select_material(facts, req, pos.position, issues)
            if line.material is None and resolve.searched(facts, req):
                no_match.append(pos.position)
            elif line.material is None:
                issues.append(f"position {pos.position}: Kataloggruppe {req.catalogue_group or '(none)'} not "
                              "searched in the ERP yet, material unknown")
        elif req is not None:
            issues.append(f"position {pos.position}: requirement incomplete, material not selected")
        lines.append(line)

    approval = resolve.discount_approval(facts, issues)
    if no_match:
        issues.append(f"no active material matches position(s) {', '.join(no_match)}: no bid (rule 3)")
        percent = approval.percent if approval and approval.min_order_value is None else ZERO
        answer = _answer(facts, "no_bid", percent, [], ZERO)
        return BidResult(answer, _dedupe(issues), lines, [])

    priced = [ln for ln in lines if ln.material is not None and ln.pieces is not None]
    for ln in priced:
        ln.base = base_unit_price(ln.material, ln.pieces, facts, issues)
    percent = _discount(facts, approval, priced, issues)
    _surcharges(facts, priced, issues)
    _setup_pools(facts, priced, issues)
    _packaging(facts, priced, issues)
    _bonus(facts, priced, issues)
    _scopes(facts, priced, issues)
    _lead_times(facts, priced, issues)

    out, incomplete, omitted, total = [], [], [], ZERO
    for ln in lines:
        if ln.material is None or ln.pieces is None:
            issues.append(f"position {ln.position}: left out (material or quantity unknown)")
            omitted.append(ln.position)
            continue
        ln.unit_price = _unit_price(ln, percent)
        if ln.unit_price is None:
            incomplete.append(ln.position)
        unit_price = ln.unit_price if ln.unit_price is not None else ZERO
        total += ln.pieces * unit_price
        out.append(_position(ln, unit_price, issues))
    answer = _answer(facts, "bid", percent, out, half_up(total))
    return BidResult(answer, _dedupe(issues), lines, incomplete, omitted)


# ---------------------------------------------------------------------------------------------------------------------
# Order level


def _discount(facts: Facts, approval, lines: list[Line], issues: list[str]) -> Decimal:
    """Rules 5 and 16: the latest approval's percent, or 0 if its minimum undiscounted order value is not reached.
    The order value is final pieces times base price, before discount, without surcharges or setup, in cents."""
    if approval is None:
        return ZERO
    if approval.min_order_value is None:
        return approval.percent
    if any(ln.base is None for ln in lines):
        issues.append("discount threshold computed without positions whose base price is unknown")
    value = half_up(sum((ln.pieces * ln.base for ln in lines if ln.base is not None), ZERO))
    return approval.percent if value >= approval.min_order_value else ZERO


def _members(facts: Facts, lines: list[Line], kind: AssignmentKind) -> dict[str, list[Line]]:
    groups: dict[str, list[Line]] = defaultdict(list)
    for ln in lines:
        a = resolve.assignment(facts, ln.position, kind)
        if a is not None:
            groups[norm_key(a.key)].append(ln)
    return groups


def _surcharges(facts: Facts, lines: list[Line], issues: list[str]):
    """Rule 28: mass surcharge per piece = surcharge mass x index rate on the scenario date. Setup fee as read."""
    for ln in lines:
        m = ln.material
        ln.setup = m.setup_fee_eur if m.setup_fee_eur is not None else ZERO
        if not norm_key(m.metal_index):
            ln.mass_surcharge = ZERO
            continue
        rate = index_rate(m, facts, issues)
        if rate is None or m.surcharge_mass_kg is None:
            if m.surcharge_mass_kg is None:
                issues.append(f"material {m.material_number}: surcharge mass unknown")
            ln.mass_surcharge = None
        else:
            ln.mass_surcharge = m.surcharge_mass_kg * rate


def _setup_pools(facts: Facts, lines: list[Line], issues: list[str]):
    """Rule 25: one pool fee allocated in cents by final pieces x allocation mass."""
    pools = resolve.latest_by_key(facts.setup_pools, facts, issues)
    for key, members in _members(facts, lines, AssignmentKind.SETUP_POOL).items():
        pool = pools.get(key)
        missing = [ln.position for ln in members if ln.material.allocation_mass_kg is None]
        if pool is None or missing:
            issues.append(f"setup pool {key}: " + ("fee not found" if pool is None
                                                    else f"allocation mass unknown for {', '.join(missing)}"))
            for ln in members:
                ln.pool = None
            continue
        weights = {ln.position: ln.pieces * ln.material.allocation_mass_kg for ln in members}
        for ln in members:
            ln.pool = ZERO
        if sum(weights.values(), ZERO) == 0:
            issues.append(f"setup pool {key}: all weights are zero, fee not allocated")
            continue
        shares = allocate_cents(pool.fee_eur, weights, pool.allocation.value)
        for ln in members:
            ln.pool = shares[ln.position]


def _packaging(facts: Facts, lines: list[Line], issues: list[str]):
    """Rule 23: ceil(final pieces / pieces per carton) cartons at the assigned tariff, per position, never pooled."""
    tariffs = resolve.latest_by_key(facts.packaging_tariffs, facts, issues)
    for key, members in _members(facts, lines, AssignmentKind.PACKAGING).items():
        tariff = tariffs.get(key)
        for ln in members:
            size = ln.material.pieces_per_carton
            if tariff is None or not size:
                issues.append(f"position {ln.position}: packaging {key}: "
                              + ("tariff not found" if tariff is None else "pieces per carton unknown"))
                ln.packaging = None
            else:
                ln.packaging = ceil_int(div(ln.pieces, size)) * tariff.eur_per_carton


def _bonus(facts: Facts, lines: list[Line], issues: list[str]):
    """Rule 26: marginal bands on the members' undiscounted goods total, allocated in cents by line goods value."""
    bonuses = resolve.latest_by_key(facts.project_bonuses, facts, issues)
    for key, members in _members(facts, lines, AssignmentKind.PROJECT_BONUS).items():
        bonus = bonuses.get(key)
        if bonus is None or any(ln.base is None for ln in members):
            issues.append(f"project bonus {key}: " + ("bands not found" if bonus is None
                                                       else "a member's base price is unknown"))
            for ln in members:
                ln.bonus = None
            continue
        values = {ln.position: ln.pieces * ln.base for ln in members}
        credit = bonus_credit(sum(values.values(), ZERO), bonus.bands)
        for ln in members:
            ln.bonus = ZERO
        if credit > 0:
            shares = allocate_cents(credit, values)
            for ln in members:
                ln.bonus = shares[ln.position]


def _scopes(facts: Facts, lines: list[Line], issues: list[str]):
    """Rule 12: a position's Konditionsgruppe decides what the discount reduces; without one, the base only."""
    rules = resolve.latest_by_key(facts.condition_groups, facts, issues)
    for key, members in _members(facts, lines, AssignmentKind.CONDITION_GROUP).items():
        rule = rules.get(key)
        if rule is None:
            issues.append(f"Konditionsgruppe {key}: rule not found; using the default (discount on base only)")
            continue
        for ln in members:
            ln.scope = rule.scope


def _lead_times(facts: Facts, lines: list[Line], issues: list[str]):
    """Rules 6, 15, 24: a matching commitment within its ceiling, else stock if it covers the pooled demand of the
    material over all positions, else production."""
    demand: dict[str, int] = defaultdict(int)
    for ln in lines:
        demand[norm_key(ln.material.material_number)] += ln.pieces
    commitments = resolve.latest_by_key(facts.delivery_commitments, facts, issues)
    for ln in lines:
        m = ln.material
        pooled = demand[norm_key(m.material_number)]
        a = resolve.assignment(facts, ln.position, AssignmentKind.DELIVERY)
        if a is not None:
            c = commitments.get(norm_key(a.key))
            if c is None:
                issues.append(f"position {ln.position}: delivery commitment {a.key} not found")
            elif norm_key(c.material_number) == norm_key(m.material_number) and pooled <= c.max_quantity:
                ln.lead_weeks = c.lead_weeks
                continue
        if m.stock is not None and m.stock >= pooled:
            ln.lead_weeks = m.stock_lead_weeks
        elif m.stock is not None:
            ln.lead_weeks = m.production_lead_weeks
        else:
            issues.append(f"material {m.material_number}: stock unknown, using production lead time")
            ln.lead_weeks = m.production_lead_weeks
        if ln.lead_weeks is None:
            issues.append(f"position {ln.position}: lead time unknown")


# ---------------------------------------------------------------------------------------------------------------------
# Position level


def _unit_price(ln: Line, percent: Decimal) -> Decimal | None:
    """Rule 27: discounted base + discounted mass surcharge + (setup + pool + packaging - bonus) / pieces, from
    unrounded components, rounded half-up once."""
    parts = (ln.base, ln.mass_surcharge, ln.setup, ln.pool, ln.packaging, ln.bonus)
    if any(p is None for p in parts):
        return None
    keep = 1 - percent / 100
    base = ln.base * keep if ln.scope in (DiscountScope.BASE, DiscountScope.BASE_AND_SURCHARGE) else ln.base
    mass = ln.mass_surcharge * keep if ln.scope == DiscountScope.BASE_AND_SURCHARGE else ln.mass_surcharge
    once = ln.setup + ln.pool + ln.packaging - ln.bonus
    per_piece = div(once, ln.pieces) if ln.pieces else ZERO
    return half_up(base + mass + per_piece)


def _position(ln: Line, unit_price: Decimal, issues: list[str]) -> dict:
    """One answer entry. An optional breakdown field is left out when it is unknown (blank = unanswered)."""
    entry = {
        "position": ln.position,
        "material_number": ln.material.material_number,
        "quantity": ln.pieces,
        "unit_price": _num(unit_price),
        "lead_time_weeks": ln.lead_weeks if ln.lead_weeks is not None else 0,
    }
    n = ln.pieces
    if ln.base is not None:
        entry["base_unit_price"] = _num(half_up(ln.base))
    if n and None not in (ln.mass_surcharge, ln.setup, ln.pool):
        entry["surcharge_per_unit"] = _num(half_up(ln.mass_surcharge + div(ln.setup + ln.pool, n)))
    if n and ln.packaging is not None:
        entry["packaging_per_unit"] = _num(half_up(div(ln.packaging, n)))
    if ln.bonus is not None:
        entry["rebate_total"] = _num(half_up(ln.bonus))
    return entry


def _answer(facts: Facts, decision: str, percent: Decimal, positions: list[dict], total: Decimal) -> dict:
    return {
        "tender_reference": facts.tender_reference,
        "decision": decision,
        "currency": "EUR",
        "discount_percent": _num(percent),
        "positions": positions,
        "total_net": _num(half_up(total)),
    }


def _num(d: Decimal) -> int | float:
    """JSON number for a Decimal: an int when whole, else a float (exact for two decimals)."""
    return int(d) if d == d.to_integral_value() else float(d)


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
