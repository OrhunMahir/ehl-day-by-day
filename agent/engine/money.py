"""Exact money helpers: half-up rounding, cent allocation by largest remainders, position ordering."""
from __future__ import annotations

import re
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, localcontext

CENT = Decimal("0.01")


def half_up(x: Decimal, places: int = 2) -> Decimal:
    return Decimal(x).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def ceil_int(x: Decimal) -> int:
    return int(Decimal(x).to_integral_value(rounding=ROUND_CEILING))


def div(a: Decimal, b: Decimal) -> Decimal:
    """Division with enough precision that later half-up rounding to cents is exact in practice."""
    with localcontext() as ctx:
        ctx.prec = 50
        return Decimal(a) / Decimal(b)


def position_key(position: str):
    """Ascending order of position identifiers: numeric parts compare as numbers ("2" < "10", "1.2" < "1.10")."""
    parts = re.split(r"(\d+)", str(position))
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in parts if p != "")


def allocate_cents(total: Decimal, weights: dict[str, Decimal],
                   method: str = "largest_remainder") -> dict[str, Decimal]:
    """Split `total` (EUR, whole cents) over `weights` proportionally, in cents: everyone gets the floor of their
    share; by default the remaining cents go one each to the largest fractional remainders, ties by ascending
    position id (rule 26, and rule 25 unless the Rüstpools mail says otherwise; see `facts.Allocation`)."""
    cents = int(half_up(total) / CENT)
    weight_sum = sum(weights.values(), Decimal(0))
    if not weights:
        return {}
    if weight_sum <= 0:
        raise ValueError("allocation weights sum to zero")
    shares = {k: div(Decimal(cents) * w, weight_sum) for k, w in weights.items()}
    floors = {k: int(s.to_integral_value(rounding=ROUND_FLOOR)) for k, s in shares.items()}
    left = cents - sum(floors.values())
    if method == "rest_to_largest_share":
        largest = min(weights, key=lambda k: (-weights[k], position_key(k)))
        floors[largest] += left
    elif method in ("largest_remainder", "largest_remainder_ties_descending"):
        if method == "largest_remainder":
            order = sorted(weights, key=lambda k: (-(shares[k] - floors[k]), position_key(k)))
        else:
            order = sorted(sorted(weights, key=position_key, reverse=True), key=lambda k: -(shares[k] - floors[k]))
        for k in order[:left]:
            floors[k] += 1
    else:
        raise ValueError(f"unknown allocation method {method!r}")
    return {k: Decimal(c) * CENT for k, c in floors.items()}
