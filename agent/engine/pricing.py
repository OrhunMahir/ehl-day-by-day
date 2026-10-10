"""Price building blocks: the valid condition and scale, currency conversion, metal index, bonus bands."""
from __future__ import annotations

from decimal import Decimal

from facts import BonusBand, Facts, IndexWindow, Material, PriceCondition, norm_key

from .money import div, half_up
from .resolve import _day, _ts, latest_by_key, not_after


def valid_condition(material: Material, facts: Facts, issues: list[str]) -> PriceCondition | None:
    """Rule 4: the price condition valid on the scenario date (bounds inclusive). If several are valid, the one that
    started last."""
    valid = [c for c in material.conditions
             if (c.valid_from is None or not_after(c.valid_from, facts.scenario_date))
             and (c.valid_to is None or not_after(facts.scenario_date, c.valid_to))]
    if not valid:
        issues.append(f"material {material.material_number}: no price condition valid on the scenario date")
        return None
    if len(valid) > 1:
        issues.append(f"material {material.material_number}: {len(valid)} conditions valid; taking the latest start")
    return max(valid, key=lambda c: _ts(c.valid_from))


def scale_price(condition: PriceCondition, pieces: int) -> Decimal | None:
    """Rules 4, 15, 22: price per piece in the condition's currency at the highest scale this position reaches."""
    reached = [s for s in condition.scales if s.min_quantity <= pieces]
    if not reached:
        return None
    best = max(reached, key=lambda s: s.min_quantity)
    return div(best.price, condition.price_unit or Decimal(1))


def base_unit_price(material: Material, pieces: int, facts: Facts, issues: list[str]) -> Decimal | None:
    """The EUR base price per piece before any discount. A foreign currency is converted with the Devisenfixing rate
    and rounded half-up to cents (rule 22); that rounded value is the base from then on. An EUR price stays exact."""
    cond = valid_condition(material, facts, issues)
    if cond is None:
        return None
    price = scale_price(cond, pieces)
    if price is None:
        issues.append(f"material {material.material_number}: no quantity scale reached by {pieces} pieces")
        return None
    currency = norm_key(cond.currency) or "EUR"
    if currency == "EUR":
        return price
    rates = latest_by_key(facts.fx_rates, facts, issues)
    rate = rates.get(norm_key(cond.fx_key))
    if rate is None:
        issues.append(f"material {material.material_number}: price in {currency}, FX key {cond.fx_key or '(none)'} "
                      "not found")
        return None
    if norm_key(rate.currency) and norm_key(rate.currency) != currency:
        issues.append(f"FX key {cond.fx_key}: rate is for {rate.currency}, condition is in {currency}")
        return None
    return half_up(price * rate.eur_per_unit)


def index_rate(material: Material, facts: Facts, issues: list[str]) -> Decimal | None:
    """Rule 28: EUR per kg of the material's metal index on the scenario date, from the latest authorized index
    record. Windows are inclusive; when none is dated, the single listed rate applies."""
    index = latest_by_key(facts.metal_indices, facts, issues).get(norm_key(material.metal_index))
    if index is None:
        issues.append(f"material {material.material_number}: metal index {material.metal_index} not found")
        return None
    day = _day(facts.scenario_date)
    hits = [w for w in index.windows if _in_window(w, day)]
    if not hits:
        issues.append(f"metal index {material.metal_index}: no rate valid on the scenario date")
        return None
    if len(hits) > 1:
        issues.append(f"metal index {material.metal_index}: {len(hits)} windows contain the scenario date")
    return max(hits, key=lambda w: (w.valid_from is not None, w.valid_from or day)).rate_eur_per_kg


def _in_window(w: IndexWindow, day) -> bool:
    return (w.valid_from is None or _day(w.valid_from) <= day) and (w.valid_to is None or day <= _day(w.valid_to))


def bonus_credit(total: Decimal, bands: list[BonusBand]) -> Decimal:
    """Rule 26: each band's percent on the part of the total inside it, summed, rounded half-up once."""
    credit = Decimal(0)
    for band in bands:
        top = total if band.upper is None else min(total, band.upper)
        part = top - band.lower
        if part > 0:
            credit += part * band.percent / 100
    return half_up(credit)
