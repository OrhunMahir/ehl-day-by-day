"""Robustness: facts damaged the way a model may deliver them must never crash the engine, and the answer must
always be one the configurator accepts (the answer schema of the challenge, plus a consistent total)."""
import random
import typing
import unittest
from dataclasses import fields, is_dataclass
from decimal import Decimal as D
from enum import Enum

from test_engine_random import invent

from engine import evaluate
from facts import Assignment, AssignmentKind, QuantityChange, TenderPosition, Unit, Withdrawal

ANSWER_KEYS = {"tender_reference", "decision", "currency", "discount_percent", "positions", "total_net"}
POSITION_KEYS = {"position", "material_number", "quantity", "unit_price", "lead_time_weeks"}
OPTIONAL = {"base_unit_price", "surcharge_per_unit", "packaging_per_unit", "rebate_total"}


def schema_problems(a: dict) -> list[str]:
    """The challenge's answer schema, checked by hand, plus what the scorer checks for consistency."""
    out = []
    if set(a) - ANSWER_KEYS - {"notes"} or ANSWER_KEYS - set(a):
        out.append(f"answer keys {sorted(a)}")
    if a.get("decision") not in ("bid", "no_bid") or a.get("currency") != "EUR":
        out.append("decision or currency")
    if not isinstance(a.get("tender_reference"), str) or len(a["tender_reference"]) > 100:
        out.append("tender_reference")
    pct = a.get("discount_percent")
    if not isinstance(pct, (int, float)) or isinstance(pct, bool) or not 0 <= pct <= 100:
        out.append(f"discount_percent {pct!r}")
    total = a.get("total_net")
    if not isinstance(total, (int, float)) or total < 0:
        out.append(f"total_net {total!r}")
    positions = a.get("positions")
    if not isinstance(positions, list) or len(positions) > 100:
        return out + ["positions"]
    if a.get("decision") == "no_bid" and (positions or total != 0):
        out.append("no_bid with positions or a total")
    ids = [p.get("position") for p in positions]
    if len(ids) != len(set(ids)):
        out.append(f"duplicate positions {ids}")
    running = D(0)
    for p in positions:
        if set(p) - POSITION_KEYS - OPTIONAL or POSITION_KEYS - set(p):
            out.append(f"position keys {sorted(p)}")
            continue
        if not isinstance(p["position"], str) or len(p["position"]) > 20 or not p["position"]:
            out.append(f"position id {p['position']!r}")
        if not isinstance(p["material_number"], str) or len(p["material_number"]) > 50:
            out.append(f"material_number {p['material_number']!r}")
        for name in ("quantity", "lead_time_weeks"):
            if not isinstance(p[name], int) or isinstance(p[name], bool) or p[name] < 0:
                out.append(f"{p['position']} {name} {p[name]!r}")
        for name in ("unit_price", *OPTIONAL):
            if name in p and (not isinstance(p[name], (int, float)) or p[name] < 0):
                out.append(f"{p['position']} {name} {p[name]!r}")
        if isinstance(p.get("quantity"), int) and isinstance(p.get("unit_price"), (int, float)):
            running += p["quantity"] * D(str(p["unit_price"]))
    # a float holds an amount to the cent only below 2**53 / 100 (about 90 trillion); the damage can go beyond that
    if isinstance(total, (int, float)) and abs(total) < 2 ** 53 / 100 and D(str(total)) != running:
        out.append(f"total {total} != sum of positions {running}")
    return out


def optional(cls, name: str) -> bool:
    hint = typing.get_type_hints(cls)[name]
    return type(None) in typing.get_args(hint)


def damage(facts, rng: random.Random):
    """Random damage: nulls, empty strings, zeros, negatives, missing records, duplicates, dangling keys."""
    def walk(obj):
        if isinstance(obj, list):
            for item in list(obj):
                if rng.random() < 0.06:
                    obj.remove(item)
                else:
                    walk(item)
        elif is_dataclass(obj) and not obj.__dataclass_params__.frozen:
            for f in fields(obj):
                value = getattr(obj, f.name)
                roll = rng.random()
                if isinstance(value, (list,)) or is_dataclass(value):
                    walk(value)
                elif roll < 0.04 and optional(type(obj), f.name):      # from_dict lets null through only here
                    setattr(obj, f.name, None)
                elif roll < 0.06 and isinstance(value, D):
                    setattr(obj, f.name, rng.choice([D(0), D(-1), D("0.001"), D(10) ** 6]))
                elif roll < 0.07 and isinstance(value, str) and not isinstance(value, Enum):
                    setattr(obj, f.name, "")
                elif roll < 0.08 and isinstance(value, int) and not isinstance(value, bool):
                    setattr(obj, f.name, rng.choice([0, -2]))
    walk(facts)
    if facts.positions and rng.random() < 0.3:
        facts.positions.append(facts.positions[0])                      # the same position twice
    if rng.random() < 0.3:
        facts.assignments.append(Assignment("nowhere", rng.choice(list(AssignmentKind)), "K-X"))
    if rng.random() < 0.3:
        facts.quantity_changes.append(QuantityChange("nowhere", D(5), "Z-1"))
    if facts.positions and rng.random() < 0.2:
        facts.quantity_changes.append(QuantityChange(facts.positions[0].position, D(-3), "Z-2"))
    if rng.random() < 0.2:
        facts.withdrawals.append(Withdrawal("Z-3", ""))
    if rng.random() < 0.2:
        facts.positions.append(TenderPosition("X" * 30, D(1), Unit.PIECE, "unknown"))   # id too long for the form
    return facts


class Robust(unittest.TestCase):
    def test_damaged_facts(self):
        for seed in range(400):
            with self.subTest(seed=seed):
                facts = damage(invent(seed % 300).facts, random.Random(seed))
                try:
                    result = evaluate(facts)
                    result.explain()
                except Exception as error:  # noqa: BLE001
                    self.fail(f"{type(error).__name__}: {error}")
                self.assertEqual(schema_problems(result.answer), [])

    def test_clean_facts_pass_the_schema_check(self):
        for seed in range(0, 300, 10):
            with self.subTest(seed=seed):
                self.assertEqual(schema_problems(evaluate(invent(seed).facts).answer), [])


if __name__ == "__main__":
    unittest.main()
