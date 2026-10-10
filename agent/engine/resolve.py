"""From raw facts to resolved ones: which records count (authority and time), the final quantity, the governing
requirement and the matching material. Every function appends what it could not decide to `issues`."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

from facts import (Assignment, AssignmentKind, Attr, Facts, Material, Requirement, SpecRevision, TenderPosition, Unit,
                   dec, mail_address, norm_key, norm_text)

from .money import position_key

_EPOCH = datetime(1, 1, 1, tzinfo=timezone.utc)


def _ts(d) -> datetime:
    """Sort key for a record date; records without a date sort first, so dated records win."""
    if d is None:
        return _EPOCH
    if isinstance(d, datetime):
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def not_after(d, limit) -> bool:
    """`d` is no later than `limit`. When either side is a plain date, the comparison is by calendar day."""
    if d is None:
        return True
    if isinstance(d, datetime) and isinstance(limit, datetime):
        return _ts(d) <= _ts(limit)
    return _day(d) <= _day(limit)


def _day(d) -> date:
    return d.date() if isinstance(d, datetime) else d


def _date(record):
    return record.source.date if record.source else None


def _sender(record) -> str:
    return mail_address(record.source.sender) if record.source else ""


# ---------------------------------------------------------------------------------------------------------------------
# Rule 21: authority and time


def rejection(record, facts: Facts) -> str | None:
    """Why rule 21 (or rule 5 for discount approvals) rules a record out, or None when it counts."""
    if norm_key(record.reference) != norm_key(facts.tender_reference):
        return "other tender reference"
    if not not_after(_date(record), facts.scenario_date):
        return "dated after the scenario date"
    sender = _sender(record)
    if not sender:
        return None
    if type(record).__name__ == "DiscountApproval":
        want = mail_address(facts.director_address)
        return "not from the sales director" if want and sender != want else None
    local = facts.authority.get(type(record).__name__)
    if not local:
        return None
    got_local, _, got_domain = sender.partition("@")
    if got_local != local.split("@")[0].lower():
        return "sender not authorized"
    if facts.bidder_domain and got_domain != facts.bidder_domain.lower().lstrip("@"):
        return "sender outside the bidder's domain"
    return None


def authorized(record, facts: Facts, issues: list[str]) -> bool:
    """The record is for the exact tender, not later than the scenario date, and from its authorized sender."""
    if rejection(record, facts) is not None:
        return False
    kind = type(record).__name__
    if _date(record) is None:
        issues.append(f"{kind} {getattr(record, 'key', '')}: no date, cannot check it against the scenario date")
    if facts.authority.get(kind) and not _sender(record):
        issues.append(f"{kind} {getattr(record, 'key', '')}: sender unknown, authority not checked")
    return True


def latest_by_key(records, facts: Facts, issues: list[str], key=lambda r: norm_key(r.key)) -> dict:
    """Rule 21: per key, the latest authorized record. A newer record replaces an older one, whatever it says."""
    best: dict = {}
    for i, r in enumerate(records):
        if not authorized(r, facts, issues):
            continue
        k = key(r)
        rank = (_ts(_date(r)), i)
        if k in best and best[k][0][0] == rank[0]:
            issues.append(f"{type(r).__name__} {k}: two records with the same date; the later one in the list wins")
        if k not in best or rank > best[k][0]:
            best[k] = (rank, r)
    return {k: v[1] for k, v in best.items()}


def discount_approval(facts: Facts, issues: list[str]):
    """Rule 5: the most recent approval from the sales director for this exact tender, or None."""
    want = mail_address(facts.director_address)
    if not want and facts.discount_approvals:
        issues.append("director_address unknown: discount approvals are not checked against their sender")
    best = None
    for i, a in enumerate(facts.discount_approvals):
        if norm_key(a.reference) != norm_key(facts.tender_reference):
            continue
        if not not_after(_date(a), facts.scenario_date):
            continue
        sender = _sender(a)
        if want and sender and sender != want:
            continue
        if want and not sender:
            issues.append("discount approval: sender unknown, not checked against the sales director")
        rank = (_ts(_date(a)), i)
        if best is None or rank > best[0]:
            best = (rank, a)
    return best[1] if best else None


# ---------------------------------------------------------------------------------------------------------------------
# Tender Q&A


def _qa_rank(entry):
    return (_ts(_date(entry)), position_key(entry.qa_number))


def withdrawn_numbers(facts: Facts) -> set[str]:
    """Rule 13: numbers of Q&A answers withdrawn by a later answer."""
    return {norm_key(w.withdraws) for w in facts.withdrawals
            if norm_key(w.withdraws) and not_after(_date(w), facts.scenario_date)}


def _live(entries, facts: Facts):
    gone = withdrawn_numbers(facts)
    return [e for e in entries
            if not (norm_key(e.qa_number) and norm_key(e.qa_number) in gone) and not_after(_date(e), facts.scenario_date)]


def assignment(facts: Facts, position: str, kind: AssignmentKind) -> Assignment | None:
    """The latest remaining Q&A assignment of this kind to the position."""
    found = [a for a in _live(facts.assignments, facts) if a.kind == kind and norm_key(a.position) == norm_key(position)]
    return max(found, key=_qa_rank) if found else None


def stated_quantity(facts: Facts, pos: TenderPosition) -> Decimal:
    """Rules 1 and 13: the tender quantity, overridden by the latest remaining Q&A change, in the stated unit."""
    changes = [c for c in _live(facts.quantity_changes, facts) if norm_key(c.position) == norm_key(pos.position)]
    return max(changes, key=_qa_rank).quantity if changes else pos.quantity


def quantity_story(facts: Facts, pos: TenderPosition) -> str:
    """How the stated quantity came about, for the explanation."""
    gone = withdrawn_numbers(facts)
    mine = sorted((c for c in facts.quantity_changes if norm_key(c.position) == norm_key(pos.position)), key=_qa_rank)
    unit = "sets" if pos.unit == Unit.SET else "pieces"
    parts = [f"tender {pos.quantity} {unit}"]
    for c in mine:
        label = c.qa_number or "change"
        day = _date(c).strftime("%Y-%m-%d") if _date(c) else "no date"
        state = (" (withdrawn)" if norm_key(c.qa_number) and norm_key(c.qa_number) in gone
                 else " (after the scenario date)" if not not_after(_date(c), facts.scenario_date) else "")
        parts.append(f"{label} {day}: {c.quantity}{state}")
    return "; ".join(parts) + f" -> {stated_quantity(facts, pos)} {unit}"


def mismatches(m: Material, req: Requirement) -> list[str]:
    """The attributes in which a material differs from the requirement (rule 3)."""
    out = [name for name, a, b in (("bore", m.bore_mm, req.bore_mm), ("stroke", m.stroke_mm, req.stroke_mm),
                                   ("pressure", m.pressure_bar, req.pressure_bar)) if not _same(a, b)]
    if m.mounting is None or norm_text(m.mounting) != norm_text(req.mounting):
        out.append("mounting")
    if _cert(m.certification) != _cert(req.certification):
        out.append("certification")
    return out


def has_withdrawn_change(facts: Facts, pos: TenderPosition) -> bool:
    gone = withdrawn_numbers(facts)
    return any(norm_key(c.qa_number) and norm_key(c.qa_number) in gone for c in facts.quantity_changes
               if norm_key(c.position) == norm_key(pos.position))


def final_pieces(facts: Facts, pos: TenderPosition, req: Requirement | None, issues: list[str]) -> int | None:
    """Rule 17: the final quantity in pieces; a Satz is converted with the governing specification's pieces per set."""
    q = stated_quantity(facts, pos)
    if pos.unit == Unit.SET:
        if req is None or req.pieces_per_set is None:
            issues.append(f"position {pos.position}: quantity is in sets, pieces per set unknown")
            return None
        q = q * req.pieces_per_set
    if q != q.to_integral_value():
        issues.append(f"position {pos.position}: quantity {q} is not a whole number of pieces")
        return None
    return int(q)


# ---------------------------------------------------------------------------------------------------------------------
# Specification


def governing_revision(facts: Facts, issues: list[str]) -> SpecRevision | None:
    """Rule 2: the revision marked current. Without exactly one such mark, the latest dated revision not after the
    scenario date among the marked ones (or among all, when none is marked)."""
    if not facts.revisions:
        return None
    marked = [r for r in facts.revisions if r.current]
    if len(marked) == 1:
        return marked[0]
    pool = marked or facts.revisions
    issues.append(f"specification: {len(marked)} revisions marked current; choosing the latest dated one")
    pool = [r for r in pool if not_after(r.date, facts.scenario_date)] or pool
    return max(pool, key=lambda r: _ts(r.date))


def requirement(facts: Facts, revision: SpecRevision | None, ref: str, issues: list[str]) -> Requirement | None:
    """Rules 14 and 20: the requirement in the governing revision, with the scoped amendments applied (latest per
    attribute) and, for usable travel, twice the per-end reserve added to the stroke."""
    if revision is None:
        issues.append(f"requirement {ref}: no governing specification revision")
        return None
    base = next((r for r in revision.requirements if norm_key(r.requirement) == norm_key(ref)), None)
    if base is None:
        issues.append(f"requirement {ref}: not in revision {revision.revision}")
        return None
    latest: dict[Attr, object] = {}
    for a in sorted(_live(facts.amendments, facts), key=_qa_rank):
        if norm_key(a.revision) == norm_key(revision.revision) and norm_key(a.requirement) == norm_key(ref):
            latest[a.attribute] = a.value
    changes = {}
    for attr, value in latest.items():
        changes[attr.value] = value if attr in (Attr.MOUNTING, Attr.CERTIFICATION) else dec(value)
    req = replace(base, **changes)
    if req.usable_travel:
        reserves = latest_by_key(facts.end_reserves, facts, issues,
                                 key=lambda r: (norm_key(r.key), norm_key(r.revision)))
        reserve = reserves.get((norm_key(req.end_reserve_key), norm_key(revision.revision)))
        if reserve is None or req.stroke_mm is None:
            issues.append(f"requirement {ref}: usable travel, but no end reserve {req.end_reserve_key or '(no key)'} "
                          f"for revision {revision.revision}")
            return replace(req, stroke_mm=None)
        req = replace(req, stroke_mm=req.stroke_mm + 2 * reserve.reserve_mm)
    return req


# ---------------------------------------------------------------------------------------------------------------------
# Material


_NO_CERT = {"", "-", "–", "—", "keine", "keine gefordert", "nicht gefordert", "ohne", "none", "n/a", "entfällt",
            "keine anforderung", "keine zertifizierung"}


def _cert(text: str | None) -> str:
    t = norm_text(text).rstrip(".")
    return "" if t in _NO_CERT else t


def _same(a, b) -> bool:
    return a is not None and b is not None and Decimal(a) == Decimal(b)


def matches(m: Material, req: Requirement) -> bool:
    return (_same(m.bore_mm, req.bore_mm) and _same(m.stroke_mm, req.stroke_mm)
            and _same(m.pressure_bar, req.pressure_bar)
            and m.mounting is not None and norm_text(m.mounting) == norm_text(req.mounting)
            and _cert(m.certification) == _cert(req.certification))


def requirement_complete(req: Requirement) -> bool:
    return None not in (req.bore_mm, req.stroke_mm, req.pressure_bar, req.mounting)


def searched(facts: Facts, req: Requirement) -> bool:
    """The ERP was searched for this requirement: its Kataloggruppe is recorded as searched or a candidate of that
    group is known. Without a Kataloggruppe, any known material counts as a search."""
    group = norm_key(req.catalogue_group)
    if not group:
        return bool(facts.materials)
    return (group in {norm_key(g) for g in facts.searched_groups}
            or any(norm_key(m.catalogue_group) == group for m in facts.materials))


def select_material(facts: Facts, req: Requirement, position: str, issues: list[str]) -> Material | None:
    """Rule 3: the active material whose bore, stroke, pressure, mounting and certification match exactly."""
    hits = [m for m in facts.materials if matches(m, req)]
    active = [m for m in hits if m.active is True]
    if not active:
        unknown = [m for m in hits if m.active is None]
        if unknown:
            issues.append(f"position {position}: matching material {unknown[0].material_number} has unknown status")
            active = unknown
    if len(active) > 1:
        numbers = ", ".join(m.material_number for m in active)
        issues.append(f"position {position}: several active materials match ({numbers}); taking the first")
    return active[0] if active else None
