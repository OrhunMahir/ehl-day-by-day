"""What the lead still has to find or verify before the bid is complete. Read from the facts and the engine's
results; it decides nothing itself. Sites are named by their role, since the final run's sites have other names."""
from __future__ import annotations

from dataclasses import dataclass, field

from facts import AssignmentKind, Facts, Unit, norm_key

from . import resolve
from .pricing import index_rate, scale_price, valid_condition

TENDER, SPEC, ERP, MAIL, BRIEF = "tender portal", "specification", "ERP", "mailbox", "brief"
_ORDER = {BRIEF: 0, TENDER: 1, SPEC: 2, ERP: 3, MAIL: 4}

_RECORD_FOR = {     # assignment kind -> (facts list, record name, authorized sender field)
    AssignmentKind.CONDITION_GROUP: ("condition_groups", "Konditionsregeln", "ConditionGroupRule"),
    AssignmentKind.PACKAGING: ("packaging_tariffs", "Verpackungstarife", "PackagingTariff"),
    AssignmentKind.DELIVERY: ("delivery_commitments", "Terminfreigabe", "DeliveryCommitment"),
    AssignmentKind.SETUP_POOL: ("setup_pools", "Rüstpools", "SetupPool"),
    AssignmentKind.PROJECT_BONUS: ("project_bonuses", "Projektbonus", "ProjectBonus"),
}
_RULE_FOR = {AssignmentKind.CONDITION_GROUP: 12, AssignmentKind.PACKAGING: 23, AssignmentKind.DELIVERY: 24,
             AssignmentKind.SETUP_POOL: 25, AssignmentKind.PROJECT_BONUS: 26}


@dataclass
class Need:
    kind: str                       # "find": a fact is missing; "verify": a value is assumed or decisive
    site: str                       # where to look, by role
    what: str
    rule: int | None = None
    positions: list[str] = field(default_factory=list)
    catalogue_group: str | None = None  # a runtime ERP search key, separate from the task's wording

    def as_dict(self) -> dict:
        out = {"kind": self.kind, "site": self.site, "what": self.what}
        if self.rule:
            out["rule"] = self.rule
        if self.positions:
            out["positions"] = self.positions
        if self.catalogue_group:
            out["catalogue_group"] = self.catalogue_group
        return out


class _Collector:
    def __init__(self):
        self.items: dict[tuple, Need] = {}

    def add(self, kind, site, what, rule=None, position=None, catalogue_group=None):
        need = self.items.setdefault((kind, site, what),
                                    Need(kind, site, what, rule, catalogue_group=catalogue_group))
        if position is not None and position not in need.positions:
            need.positions.append(position)

    def result(self) -> list[Need]:
        return sorted(self.items.values(),
                      key=lambda n: (n.kind != "find", _ORDER[n.site], not n.what.startswith("search")))


def _sender(facts: Facts, record: str) -> str:
    local = facts.authority.get(record, "")
    return f" from {local}@{facts.bidder_domain or '<bidder domain>'}" if local else ""


def needs(facts: Facts, result) -> list[Need]:
    c = _Collector()
    if not facts.director_address:
        c.add("find", BRIEF, "director_address: the sales director's mail address", 5)
    if not facts.bidder_domain:
        c.add("find", BRIEF, "bidder_domain: the domain of our own mailbox", 21)
    if not facts.positions:
        c.add("find", TENDER, "the positions of the bill of quantities (number, quantity, unit, requirement)", 1)
    if not (facts.quantity_changes or facts.withdrawals or facts.amendments or facts.assignments):
        c.add("verify", TENDER, "bidder Q&A read? No quantity change, withdrawal, amendment or key recorded yet", 1)
    approval = resolve.discount_approval(facts, [])
    unpriced = result.omitted or result.incomplete or any(ln.base is None for ln in result.lines)
    if approval is not None and approval.min_order_value is not None and unpriced:
        c.add("verify", MAIL, "the discount's minimum order value was checked with positions still unpriced; it is "
                             "decided again once they are complete", 16)
    if approval is None:
        c.add("verify", MAIL, "no discount approval for this exact tender reference found (inbox and archive); "
                             "without one the discount is 0 %", 5)

    revision = resolve.governing_revision(facts, [])
    if not facts.revisions:
        c.add("find", SPEC, "the specification revisions and which one is marked current", 2)
    elif sum(1 for r in facts.revisions if r.current) != 1:
        c.add("find", SPEC, "which specification revision the site marks as current/valid", 2)
    if revision is not None:
        # A known search key can be used while positions and technical attributes are still being recorded.
        # Once positions are known, unrelated requirements no longer create ERP work.
        wanted: dict[str, list[str]] = {}
        for pos in facts.positions:
            wanted.setdefault(norm_key(pos.requirement), []).append(pos.position)
        relevant = ([r for r in revision.requirements if norm_key(r.requirement) in wanted]
                    if facts.positions else revision.requirements)
        for requirement in relevant:
            if norm_key(requirement.catalogue_group) and not resolve.searched(facts, requirement):
                for position in wanted.get(norm_key(requirement.requirement), [None]):
                    _search_need(c, requirement.catalogue_group, position)

    lines = {norm_key(ln.position): ln for ln in result.lines}
    for p in result.omitted:
        if norm_key(p) not in lines:
            c.add("find", TENDER, f"position number {p!r} is not usable (1 to 20 characters, listed once): re-read it",
                  1, p)
        else:
            ln = lines[norm_key(p)]
            if ln.material is not None and not (ln.material.material_number or "").strip():
                c.add("find", ERP, "the material number of the matching material", 3, p)
            elif ln.material is not None and len(ln.material.material_number.strip()) > 50:
                c.add("find", ERP, f"material number {ln.material.material_number!r} is too long: re-read it", 3, p)
    for pos in facts.positions:
        ln = lines.get(norm_key(pos.position))
        if ln is None:
            continue
        req = resolve.requirement(facts, revision, pos.requirement, []) if revision else None
        per_set_missing = pos.unit == Unit.SET and (req is None or req.pieces_per_set is None)
        if ln.pieces is None and not per_set_missing:
            c.add("find", TENDER, f"the quantity of position {pos.position}: re-read the tender quantity and the Q&A "
                                  "changes (it came out negative or not a whole number of pieces)", 1, pos.position)
        _position(facts, revision, pos, ln, c)
    _keys(facts, result, c)
    for ln in result.lines:
        if ln.negative_price:
            c.add("find", ERP, f"re-read the price inputs of position {ln.position} (scale price, price unit, rates, "
                               "masses, fees): the unit price came out negative", 27, ln.position)
    if result.answer.get("decision") == "no_bid":
        c.add("verify", ERP, "the bid is a no_bid: re-check the values named after 'closest:' in issues before "
                            "submitting it", 3)
    return c.result()


def _position(facts: Facts, revision, pos, ln, c: _Collector):
    p = pos.position
    if revision is None:
        return
    base = next((r for r in revision.requirements if norm_key(r.requirement) == norm_key(pos.requirement)), None)
    if not pos.requirement or base is None:
        c.add("find", SPEC, f"requirement {pos.requirement or '(which one?)'} in revision {revision.revision}", 2, p)
        return
    if pos.unit == Unit.SET and base.pieces_per_set is None:
        c.add("find", SPEC, f"pieces per set of requirement {base.requirement}", 17, p)
    if base.usable_travel:
        reserves = resolve.latest_by_key(facts.end_reserves, facts, [],
                                         key=lambda r: (norm_key(r.key), norm_key(r.revision)))
        if (norm_key(base.end_reserve_key), norm_key(revision.revision)) not in reserves:
            c.add("find", MAIL, f"Technikfreigabe: end reserve {base.end_reserve_key or '(key?)'} for revision "
                                f"{revision.revision}{_sender(facts, 'EndReserve')}", 20, p)
    req = resolve.requirement(facts, revision, pos.requirement, [])
    if req is None:
        return
    missing = [name for name, value in (("bore", req.bore_mm), ("pressure", req.pressure_bar),
                                        ("mounting", req.mounting)) if value is None]
    if base.stroke_mm is None:
        missing.append("stroke")
    if missing:
        c.add("find", SPEC, f"{', '.join(missing)} of requirement {req.requirement}", 3, p)
        return
    if not resolve.requirement_complete(req):
        return
    if not norm_key(req.catalogue_group):
        c.add("find", SPEC, f"the Kataloggruppe of requirement {req.requirement} (the ERP search key)", 10, p)
    if ln.material is None:
        for candidate in resolve.unknown_matching_materials(facts, req):
            c.add("find", ERP, f"status (active/released?) of matching material {candidate.material_number}", 3, p,
                  catalogue_group=req.catalogue_group or None)
        if not resolve.searched(facts, req):
            if norm_key(req.catalogue_group):
                _search_need(c, req.catalogue_group, p)
        return
    _material(facts, ln, c)


def _search_need(c: _Collector, group: str, position: str | None = None):
    c.add("find", ERP,
          f"search Kataloggruppe {group}: record every result with the attributes the list shows; open the details "
          "(status, stock, conditions, scales) of matching candidates and candidates not yet ruled out",
          10, position, catalogue_group=group)


def _material(facts: Facts, ln, c: _Collector):
    m, p, name = ln.material, ln.position, ln.material.material_number
    if m.active is None:
        c.add("verify", ERP, f"status (active?) of material {name}", 3, p)
    cond = valid_condition(m, facts, []) if m.conditions else None
    if not m.conditions:
        c.add("find", ERP, f"price conditions of {name} (validity, currency, FX key, price unit, all scales)", 4, p)
    elif cond is None:
        c.add("find", ERP, f"a price condition of {name} valid on the scenario date (all validity periods)", 4, p)
    elif ln.pieces is not None and scale_price(cond, ln.pieces) is None:
        c.add("find", ERP, f"quantity scales of {name} down to {ln.pieces} pieces", 4, p)
    elif norm_key(cond.currency) not in ("", "EUR"):
        if norm_key(cond.fx_key) not in resolve.latest_by_key(facts.fx_rates, facts, []):
            c.add("find", MAIL, f"Devisenfixing: rate for FX key {cond.fx_key or '(the key the condition names)'}"
                                f"{_sender(facts, 'FxRate')}", 22, p)
    if norm_key(m.metal_index):
        if m.surcharge_mass_kg is None:
            c.add("find", ERP, f"surcharge mass (Zuschlagsmasse, kg per piece) of {name}", 28, p)
        if index_rate(m, facts, []) is None:
            c.add("find", MAIL, f"Preisindizes: rate of index {m.metal_index} valid on the scenario date"
                                f"{_sender(facts, 'MetalIndex')}", 28, p)
    elif m.surcharge_mass_kg is not None:
        c.add("verify", ERP, f"metal index of {name}: a surcharge mass is recorded but no index code", 28, p)
    if m.stock is None:
        c.add("find", ERP, f"stock of {name}", 6, p)
    if m.stock_lead_weeks is None or m.production_lead_weeks is None:
        c.add("find", ERP, f"stock and production lead times of {name}", 6, p)
    if m.setup_fee_eur is None:
        c.add("verify", ERP, f"setup fee (Rüstkosten) of {name}: none recorded, 0 assumed", 27, p)
    if resolve.assignment(facts, p, AssignmentKind.PACKAGING) and not m.pieces_per_carton:
        c.add("find", ERP, f"pieces per carton (Packinhalt) of {name}", 23, p)
    if resolve.assignment(facts, p, AssignmentKind.SETUP_POOL) and m.allocation_mass_kg is None:
        c.add("find", ERP, f"allocation mass (Umlagegewicht, kg per piece) of {name}", 25, p)


def _shared(facts: Facts, result, c: _Collector):
    """A setup pool or project bonus is split over its members: one member's missing input blocks all of them."""
    groups: dict[tuple, list] = {}
    for ln in result.lines:
        for kind in (AssignmentKind.SETUP_POOL, AssignmentKind.PROJECT_BONUS):
            a = resolve.assignment(facts, ln.position, kind)
            if a is not None and ln.material is not None and ln.pieces is not None:
                groups.setdefault((kind, norm_key(a.key)), []).append(ln)
    for (kind, key), members in groups.items():
        if kind == AssignmentKind.SETUP_POOL:
            blocking = [ln for ln in members
                        if ln.material.allocation_mass_kg is None or ln.material.allocation_mass_kg < 0]
            what = "allocation mass (Umlagegewicht)"
        else:
            blocking = [ln for ln in members if ln.base is None or ln.base <= 0 or ln.pieces <= 0]
            what = "base price and quantity"
        if blocking and any((ln.pool if kind == AssignmentKind.SETUP_POOL else ln.bonus) is None for ln in members):
            names = ", ".join(f"{ln.material.material_number} (position {ln.position})" for ln in blocking)
            label = "setup pool" if kind == AssignmentKind.SETUP_POOL else "project bonus"
            for ln in members:
                c.add("find", ERP, f"{what} of {names}: the {label} {key} is shared over its members",
                      _RULE_FOR[kind], ln.position)


def _keys(facts: Facts, result, c: _Collector):
    _shared(facts, result, c)
    for ln in result.lines:
        for kind, (attr, label, record) in _RECORD_FOR.items():
            a = resolve.assignment(facts, ln.position, kind)
            if a is None:
                continue
            if not norm_key(a.key):
                c.add("find", TENDER, f"the key of the {label} assignment (recorded empty)", _RULE_FOR[kind],
                      ln.position)
                continue
            if norm_key(a.key) not in resolve.latest_by_key(getattr(facts, attr), facts, []):
                c.add("find", MAIL, f"{label}: record for key {a.key}{_sender(facts, record)}", _RULE_FOR[kind],
                      ln.position)
