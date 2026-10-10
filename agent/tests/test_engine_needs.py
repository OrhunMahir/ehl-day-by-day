"""The checklist of what is still missing (engine/needs.py): every gap the engine meets is named."""
import copy
import random
import unittest
from dataclasses import replace

from test_engine_base import core_facts
from test_engine_random import invent
from test_engine_robust import damage

from engine import evaluate


def unexplained(result) -> list[str]:
    """Positions left out or incompletely priced for which no "find" entry names the position or the whole bid."""
    needs = [n for n in result.needed() if n["kind"] == "find"]
    covered = {p for n in needs for p in n.get("positions", [])}
    global_need = any(not n.get("positions") for n in needs)
    return [p for p in result.omitted + result.incomplete if p not in covered and not global_need]


REMOVALS = {
    "fx rate": lambda f: f.fx_rates.clear(),
    "metal index": lambda f: f.metal_indices.clear(),
    "packaging tariff": lambda f: f.packaging_tariffs.clear(),
    "setup pool": lambda f: f.setup_pools.clear(),
    "project bonus": lambda f: f.project_bonuses.clear(),
    "condition group rule": lambda f: f.condition_groups.clear(),
    "delivery commitment": lambda f: f.delivery_commitments.clear(),
    "end reserve": lambda f: f.end_reserves.clear(),
    "price conditions": lambda f: [setattr(m, "conditions", []) for m in f.materials],
    "stock": lambda f: [setattr(m, "stock", None) for m in f.materials],
    "carton size": lambda f: [setattr(m, "pieces_per_carton", None) for m in f.materials],
    "allocation mass": lambda f: [setattr(m, "allocation_mass_kg", None) for m in f.materials],
    "surcharge mass": lambda f: [setattr(m, "surcharge_mass_kg", None) for m in f.materials],
    "materials": lambda f: (f.materials.clear(), f.searched_groups.clear()),
    "pieces per set": lambda f: [setattr(r, "pieces_per_set", None) for v in f.revisions for r in v.requirements],
    "current mark": lambda f: [setattr(v, "current", None) for v in f.revisions],
    "revisions": lambda f: f.revisions.clear(),
}


class Needs(unittest.TestCase):
    def test_complete_facts_need_nothing(self):
        result = evaluate(core_facts())
        self.assertTrue(result.complete)
        self.assertEqual([n for n in result.needed() if n["kind"] == "find"], [])

    def test_each_kind_of_missing_fact_is_named(self):
        for name, remove in REMOVALS.items():
            for seed in range(0, 300, 15):
                with self.subTest(removed=name, seed=seed):
                    facts = copy.deepcopy(invent(seed).facts)
                    remove(facts)
                    result = evaluate(facts)
                    self.assertEqual(unexplained(result), [])
                    if result.omitted or result.incomplete:
                        self.assertFalse(result.complete)

    def test_damaged_facts_gaps_are_named(self):
        for seed in range(200):
            with self.subTest(seed=seed):
                result = evaluate(damage(copy.deepcopy(invent(seed % 300).facts), random.Random(seed)))
                self.assertEqual(unexplained(result), [], result.issues)

    def test_examples(self):
        f = core_facts()
        f.director_address = ""
        f.materials = [m for m in f.materials if m.catalogue_group != "G-B"]
        f.materials[0] = replace(f.materials[0], conditions=[])
        needs = {n["what"]: n for n in evaluate(f).needed()}
        self.assertIn("director_address: the sales director's mail address", needs)
        search = next(n for w, n in needs.items() if w.startswith("search Kataloggruppe G-B"))
        self.assertEqual((search["kind"], search["site"], search["positions"], search["rule"]),
                         ("find", "ERP", ["2"], 10))
        self.assertTrue(any(w.startswith("price conditions of M-A1") for w in needs))

    def test_no_bid_asks_for_a_recheck(self):
        f = core_facts()
        f.materials = [m for m in f.materials if m.material_number != "M-B1"]
        f.searched_groups = ["G-B"]
        result = evaluate(f)
        self.assertEqual(result.answer["decision"], "no_bid")
        self.assertTrue(any(n["kind"] == "verify" and "no_bid" in n["what"] for n in result.needed()))


if __name__ == "__main__":
    unittest.main()
