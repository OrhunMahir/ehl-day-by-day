"""A plain-text account of a bid for the run log: what each value was computed from and which records counted.
It reads the facts and the engine's results; it decides nothing itself."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from facts import AssignmentKind, Facts, Unit, norm_key

from . import resolve
from .money import half_up
from .pricing import scale_price, valid_condition

RECORD_LISTS = ("discount_approvals", "condition_groups", "metal_indices", "packaging_tariffs",
                "delivery_commitments", "fx_rates", "setup_pools", "project_bonuses", "end_reserves")


def _when(d) -> str:
    if d is None:
        return "no date"
    if isinstance(d, datetime):
        return d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return d.isoformat()


def _money(x: Decimal | None) -> str:
    return "unknown" if x is None else str(half_up(x))


def _src(record) -> str:
    s = record.source
    if s is None:
        return "no source"
    parts = [_when(s.date), s.sender or "", s.site, s.document]
    return ", ".join(p for p in parts if p)


def explain(facts: Facts, result) -> str:
    out: list[str] = []
    a = result.answer
    out.append(f"Tender {facts.tender_reference}, scenario date {_when(facts.scenario_date)}: "
               f"{a['decision']}, discount {a['discount_percent']} %, total {a['total_net']} EUR")
    out += _discount(facts, result)
    revision = resolve.governing_revision(facts, [])
    if revision:
        how = "marked current" if revision.current else "latest dated, none marked current"
        out.append(f"Specification: revision {revision.revision} ({how})")
    by_id = {norm_key(p.position): p for p in facts.positions}
    pooled: dict[str, int] = defaultdict(int)
    for ln in result.lines:
        if ln.material is not None and ln.pieces is not None:
            pooled[norm_key(ln.material.material_number)] += ln.pieces
    for ln in result.lines:
        out += _position(facts, revision, by_id[norm_key(ln.position)], ln, pooled)
    out += _records(facts)
    if result.issues:
        out.append("Issues:")
        out += [f"  - {i}" for i in result.issues]
    return "\n".join(out)


def _discount(facts: Facts, result) -> list[str]:
    approval = resolve.discount_approval(facts, [])
    if approval is None:
        return ["Discount: no approval for this exact reference counts -> 0 %"]
    text = f"Discount: approval {approval.percent} % ({_src(approval)})"
    if approval.min_order_value is not None:
        bases = [ln for ln in result.lines if ln.base is not None and ln.pieces is not None]
        value = half_up(sum((ln.pieces * ln.base for ln in bases), Decimal(0)))
        met = "met" if value >= approval.min_order_value else "NOT met -> 0 %"
        text += f", needs order value >= {approval.min_order_value}, order value {value}: {met}"
    return [text]


def _position(facts: Facts, revision, pos, ln, pooled) -> list[str]:
    req = resolve.requirement(facts, revision, pos.requirement, [])
    per_set = f" x {req.pieces_per_set} pieces per set" if req and pos.unit == Unit.SET else ""
    out = [f"Position {ln.position}: {resolve.quantity_story(facts, pos)}{per_set} = {ln.pieces} pieces"]
    if req is None:
        return out + [f"  requirement {pos.requirement}: not found"]
    travel = " (usable travel + 2 x end reserve)" if req.usable_travel else ""
    out.append(f"  requirement {req.requirement}: bore {req.bore_mm}, stroke {req.stroke_mm}{travel}, pressure "
               f"{req.pressure_bar}, mounting {req.mounting}, certification {req.certification or 'none'}, "
               f"group {req.catalogue_group or '-'}")
    group = norm_key(req.catalogue_group)
    others = [m for m in facts.materials
              if m is not ln.material and (not group or norm_key(m.catalogue_group) == group)]
    rejected = [f"{m.material_number} ({_why_not(m, req)})" for m in others[:8]]
    if len(others) > 8:
        rejected.append(f"{len(others) - 8} more")
    chosen = ln.material.material_number if ln.material else "none"
    out.append(f"  material {chosen}" + (f"; not: {'; '.join(rejected)}" if rejected else ""))
    m = ln.material
    if m is None or ln.pieces is None:
        return out
    cond = valid_condition(m, facts, [])
    if cond is not None:
        per_piece = scale_price(cond, ln.pieces)
        reached = [s.min_quantity for s in cond.scales if s.min_quantity <= ln.pieces]
        fx = f" {cond.currency}, FX key {cond.fx_key}" if norm_key(cond.currency) not in ("", "EUR") else " EUR"
        out.append(f"  price condition {_when(cond.valid_from)} .. {_when(cond.valid_to)}{fx}, per {cond.price_unit} "
                   f"pieces, scale "
                   f"from {max(reached) if reached else '-'} -> {per_piece} per piece; base {_money(ln.base)} EUR")
    index = f" ({m.surcharge_mass_kg} kg x index {m.metal_index})" if m.metal_index else ""
    cartons = f" ({m.pieces_per_carton} per carton)" if m.pieces_per_carton else ""
    out.append(f"  per piece before discount: base {_money(ln.base)}, mass surcharge {_money(ln.mass_surcharge)}"
               f"{index}; whole position: setup {_money(ln.setup)}, setup pool {_money(ln.pool)}, packaging "
               f"{_money(ln.packaging)}{cartons}, project bonus {_money(ln.bonus)}")
    group_key = resolve.assignment(facts, ln.position, AssignmentKind.CONDITION_GROUP)
    out.append(f"  discount applies to: {ln.scope.value}"
               + (f" (Konditionsgruppe {group_key.key})" if group_key else " (default, no Konditionsgruppe)")
               + f"; unit price {_money(ln.unit_price)}")
    demand = pooled[norm_key(m.material_number)]
    text = (f"  lead time {ln.lead_weeks} weeks: stock {m.stock}, pooled demand {demand}, stock "
            f"{m.stock_lead_weeks} / production {m.production_lead_weeks} weeks")
    assigned = resolve.assignment(facts, ln.position, AssignmentKind.DELIVERY)
    if assigned:
        record = resolve.latest_by_key(facts.delivery_commitments, facts, []).get(norm_key(assigned.key))
        text += (f"; commitment {assigned.key}: " + ("no record counts" if record is None else
                 f"{record.material_number}, up to {record.max_quantity} pieces, {record.lead_weeks} weeks"))
    out.append(text)
    return out


def _why_not(m, req) -> str:
    differs = resolve.mismatches(m, req)
    if differs:
        return ", ".join(differs) + " differ"
    return "inactive" if m.active is False else "status unknown"


def _record_key(name: str):
    def approval(_record):
        return "approval"

    def with_revision(record):
        return f"{norm_key(record.key)} / {norm_key(record.revision)}"

    def plain(record):
        return norm_key(record.key)

    return {"discount_approvals": approval, "end_reserves": with_revision}.get(name, plain)


def _records(facts: Facts) -> list[str]:
    out = ["Mail records:"]
    for name in RECORD_LISTS:
        records = getattr(facts, name)
        if not records:
            continue
        keyfn = _record_key(name)
        used = ({"approval": resolve.discount_approval(facts, [])} if name == "discount_approvals"
                else resolve.latest_by_key(records, facts, [], key=keyfn))
        groups = defaultdict(list)
        for r in records:
            groups[keyfn(r)].append(r)
        for key, items in groups.items():
            chosen = used.get(key)
            reasons = Counter(resolve.rejection(r, facts) or "older" for r in items if r is not chosen)
            dropped = ", ".join(f"{n} {why}" for why, n in reasons.items())
            head = f"  {type(items[0]).__name__} {key}: "
            out.append(head + (f"uses {_src(chosen)}" if chosen else "none counts")
                       + (f"; ignored {dropped}" if dropped else ""))
    return out if len(out) > 1 else []
