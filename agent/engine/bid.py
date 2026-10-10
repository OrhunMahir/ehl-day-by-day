"""From facts to the answer object. Pure and deterministic: the same facts always give the same bid."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from decimal import Decimal

from facts import AssignmentKind, DiscountScope, Facts, Material, Requirement, TenderPosition, norm_key

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
    negative_price: bool = False            # the computed unit price was negative: an input is implausible
    tender: TenderPosition | None = None
    requirement: Requirement | None = None  # as it governs this position (amendments and end reserves applied)
    travel: Decimal | None = None           # the usable travel, when the end reserve that turns it into a stroke
                                            # is not known yet
    guessed: bool = False                   # `material` is the nearest candidate, not an exact match (yet)


@dataclass
class BidResult:
    answer: dict
    issues: list[str] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)   # positions whose material is not confirmed or whose unit
                                                          # price or quantity could not be fully computed: they are
                                                          # in the answer with what is known
    omitted: list[str] = field(default_factory=list)      # tender positions not in the answer (an unusable position
                                                          # number); mark them in the form as "Position auslassen"
    facts: Facts | None = None

    def needed(self) -> list[dict]:
        """What the lead still has to find ("find") or check ("verify"), in the order to look for it; empty "find"
        entries mean the facts are complete."""
        from .needs import needs
        return [n.as_dict() for n in needs(self.facts, self)]

    @property
    def complete(self) -> bool:
        """No fact is missing: nothing to find, no position omitted or incompletely priced."""
        return not self.omitted and not self.incomplete and not any(n["kind"] == "find" for n in self.needed())

    def explain(self) -> str:
        """Plain text for the run log: what each value was computed from and which records counted."""
        from .explain import explain
        return explain(self.facts, self)


def build_bid(facts: Facts) -> dict:
    """The answer object for the run's answer schema."""
    return evaluate(facts).answer


def evaluate(facts: Facts, allow_no_bid: bool = True) -> BidResult:
    """The answer object plus what could not be decided (`issues`) and the per-position working (`lines`).

    The answer is always complete: every position of the bill of quantities is in it with everything that is known,
    because every answer field scores on its own. A position whose material has no exact match yet is bid with the
    nearest candidate, or with none (`material_number` "": chosen in the form from the requirement); a price
    component that is not known counts as 0 in the unit price. Such positions are listed in `incomplete`. The
    decision is `no_bid` only when a searched requirement has no matching active material (rule 3) and
    `allow_no_bid` is true."""
    issues: list[str] = []
    revision = resolve.governing_revision(facts, issues)
    positions = _usable_positions(facts, issues)

    lines: list[Line] = []
    no_match: list[str] = []
    for pos in positions:
        line = Line(pos.position, tender=pos)
        req = resolve.requirement(facts, revision, pos.requirement, issues)
        line.requirement = req
        if req is not None and req.usable_travel and req.stroke_mm is None:
            stated = next((r for r in revision.requirements if norm_key(r.requirement) == norm_key(req.requirement)), None)
            line.travel = stated.stroke_mm if stated is not None else None
        line.pieces = resolve.final_pieces(facts, pos, req, issues)
        if line.pieces is not None and line.pieces < 0:
            issues.append(f"position {pos.position}: negative quantity {line.pieces}, treated as unknown")
            line.pieces = None
        if req is not None and resolve.requirement_complete(req):
            line.material = resolve.select_material(facts, req, pos.position, issues)
            if line.material is None and resolve.searched(facts, req):
                no_match.append(pos.position)
                issues.append(f"position {pos.position}: no active material matches requirement {req.requirement}; "
                              f"closest: {resolve.closest(facts, req)}. Re-check these values before a no_bid.")
            elif line.material is None:
                issues.append(f"position {pos.position}: Kataloggruppe {req.catalogue_group or '(none)'} not "
                              "searched in the ERP yet, material unknown")
        elif req is not None:
            issues.append(f"position {pos.position}: requirement incomplete, material not selected")
        lines.append(line)

    approval = resolve.discount_approval(facts, issues)
    if no_match and allow_no_bid:
        issues.append(f"no active material matches position(s) {', '.join(no_match)}: no bid (rule 3)")
        percent = _valid_percent(approval.percent, issues) if approval and approval.min_order_value is None else ZERO
        answer = _answer(facts, "no_bid", percent, [], ZERO)
        return BidResult(answer, _dedupe(issues), lines, [], [], facts)

    for ln in lines:  # no exact match yet: bid with the nearest candidate rather than without the position
        if ln.material is None and ln.requirement is not None:
            ln.material = resolve.best_candidate(facts, ln.requirement)
            if ln.material is not None:
                ln.guessed = True
                differs = ", ".join(resolve.mismatches(ln.material, ln.requirement)) or "status"
                issues.append(f"position {ln.position}: no confirmed match yet; bid with the nearest candidate "
                              f"{ln.material.material_number} (differs in {differs})")

    priced = [ln for ln in lines if ln.material is not None and ln.pieces is not None]
    for ln in priced:
        ln.base = base_unit_price(ln.material, ln.pieces, facts, issues)
    percent = _discount(approval, priced, len(lines), issues)
    _surcharges(facts, priced, issues)
    _setup_pools(facts, priced, issues)
    _packaging(facts, priced, issues)
    _bonus(facts, priced, issues)
    _scopes(facts, priced, issues)
    _lead_times(facts, priced, issues)

    out, incomplete, omitted, total = [], [], list(_dropped_ids(facts, positions)), ZERO
    for ln in lines:
        quantity = ln.pieces if ln.pieces is not None else _stated_pieces(facts, ln)
        ln.unit_price = _unit_price(ln, percent) if ln.material is not None and ln.pieces is not None else None
        if ln.unit_price is not None and ln.unit_price < 0:
            issues.append(f"position {ln.position}: unit price {ln.unit_price} is negative; a component is wrong")
            ln.unit_price = None
            ln.negative_price = True
        if ln.material is None or ln.guessed or ln.pieces is None or ln.unit_price is None:
            incomplete.append(ln.position)
        unit_price = ln.unit_price if ln.unit_price is not None else _known_part(ln, percent)
        total += quantity * unit_price
        out.append(_position(ln, quantity, unit_price, issues))
    answer = _answer(facts, "bid", percent, out, half_up(total))
    return BidResult(answer, _dedupe(issues), lines, incomplete, omitted, facts)


# ---------------------------------------------------------------------------------------------------------------------
# Order level


def _discount(approval, lines: list[Line], positions: int, issues: list[str]) -> Decimal:
    """Rules 5 and 16: the latest approval's percent, or 0 if its minimum undiscounted order value is not reached.
    The order value is final pieces times base price, before discount, without surcharges or setup, in cents.
    `positions` counts all positions: one not priced yet makes the value too low, which is reported."""
    if approval is None:
        return ZERO
    if _valid_percent(approval.percent, issues) != approval.percent:
        return ZERO
    if approval.min_order_value is None:
        return approval.percent
    if any(ln.base is None for ln in lines) or len(lines) < positions:
        issues.append("discount threshold computed without positions whose base price or material is unknown: "
                      "the order value may be too low")
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
        # the ordinary setup fee: the material's own plus a one-time fee set for the position (rules 25 and 27)
        own = ln.tender.setup_fee_eur if ln.tender is not None and ln.tender.setup_fee_eur is not None else ZERO
        ln.setup = (m.setup_fee_eur if m.setup_fee_eur is not None else ZERO) + own
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
        if any(w < 0 for w in weights.values()) or sum(weights.values(), ZERO) <= 0:
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
        if any(v < 0 for v in values.values()) or sum(values.values(), ZERO) <= 0:
            issues.append(f"project bonus {key}: goods values not usable for the allocation")
            for ln in members:
                ln.bonus = None
            continue
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


def _stated_pieces(facts: Facts, ln: Line) -> int:
    """The quantity to enter when the pieces are not known (e.g. sets whose size is not read yet): the tender's own
    figure, which may still be right, rather than nothing."""
    try:
        q = resolve.stated_quantity(facts, ln.tender)
        return max(int(half_up(q, 0)), 0)
    except Exception:  # noqa: BLE001 - no usable figure at all
        return 0


def _known_part(ln: Line, percent: Decimal) -> Decimal:
    """The unit price from the components that are known, the others counted as 0; never negative."""
    if ln.material is None or not ln.pieces:
        return ZERO
    known = replace(ln, **{name: (getattr(ln, name) if getattr(ln, name) is not None else ZERO)
                           for name in ("base", "mass_surcharge", "setup", "pool", "packaging", "bonus")})
    price = _unit_price(known, percent)
    return price if price is not None and price >= 0 else ZERO


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


def _position(ln: Line, quantity: int, unit_price: Decimal, issues: list[str]) -> dict:
    """One answer entry. An optional breakdown field is left out when it is unknown (blank = unanswered). Without a
    material, `material_number` is "" and the form's own selection decides it."""
    lead = ln.lead_weeks
    if lead is None or lead < 0:
        if lead is not None:
            issues.append(f"position {ln.position}: lead time {lead} weeks is negative")
        lead = 0
    number = (ln.material.material_number or "").strip() if ln.material is not None else ""
    if len(number) > 50:
        issues.append(f"position {ln.position}: material number {number!r} is too long for the form; left empty")
        number = ""
    entry = {
        "position": ln.position,
        "material_number": number,
        "quantity": quantity,
        "unit_price": _num(unit_price),
        "lead_time_weeks": lead,
    }
    n = ln.pieces
    optional = {}
    if ln.base is not None:
        optional["base_unit_price"] = half_up(ln.base)
    if n and None not in (ln.mass_surcharge, ln.setup, ln.pool):
        optional["surcharge_per_unit"] = half_up(ln.mass_surcharge + div(ln.setup + ln.pool, n))
    if n and ln.packaging is not None:
        optional["packaging_per_unit"] = half_up(div(ln.packaging, n))
    if ln.bonus is not None:
        optional["rebate_total"] = half_up(ln.bonus)
    for name, value in optional.items():
        if value < 0:
            issues.append(f"position {ln.position}: {name} {value} is negative, left blank")
        else:
            entry[name] = _num(value)
    return entry


def _usable_positions(facts: Facts, issues: list[str]) -> list[TenderPosition]:
    """Positions the form can take: a non-empty id of at most 20 characters, a material number of at most 50 (checked
    later), each id once. A repeated id keeps its last entry."""
    by_id: dict[str, TenderPosition] = {}
    for pos in facts.positions:
        pid = (pos.position or "").strip()
        if not pid or len(pid) > 20:
            issues.append(f"position {pid!r}: not a usable position number (1 to 20 characters), left out")
            continue
        if norm_key(pid) in by_id:
            issues.append(f"position {pid}: listed twice, the last entry counts")
        by_id[norm_key(pid)] = pos
    return sorted(by_id.values(), key=lambda p: position_key(p.position))


def _dropped_ids(facts: Facts, kept: list[TenderPosition]) -> list[str]:
    kept_ids = {norm_key(p.position) for p in kept}
    out = []
    for pos in facts.positions:
        pid = (pos.position or "").strip()
        if pid and norm_key(pid) not in kept_ids and pid not in out:
            out.append(pid)
    return out


def _valid_percent(percent: Decimal, issues: list[str]) -> Decimal:
    if percent is None or not 0 <= percent <= 100:
        issues.append(f"discount approval: percent {percent} is not between 0 and 100, using 0 %")
        return ZERO
    return percent


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
