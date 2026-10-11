"""The checklist of what is still missing (engine/needs.py): every gap the engine meets is named."""
import copy
import random
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

from test_engine_base import core_facts, material
from test_engine_random import invent
from test_engine_robust import damage

from engine import evaluate
from facts import Requirement, SpecRevision


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


def undecided():
    """core_facts() before the ERP is searched and before the current mark is read. R-1 names another group for
    requirement A than R-2 and the same one for B, the older R-0 names R-1's group for A too, and R-1 also lists a
    requirement C that no position references."""
    f = core_facts()
    f.materials, f.searched_groups = [], []
    r1 = f.revisions[0]
    r1.requirements = [replace(r1.requirements[0], catalogue_group="G-OLD"),
                       replace(r1.requirements[1], catalogue_group="G-B"),
                       Requirement("C", D(32), D(100), D(100), "Flansch", None, catalogue_group="G-C")]
    f.revisions.insert(0, SpecRevision("R-0", date=date(2030, 1, 2),
                                       requirements=[replace(r1.requirements[0], stroke_mm=D(200))]))
    for v in f.revisions:
        v.current = None
    return f


def searches(f) -> list[str]:
    """The catalogue groups the ERP is asked to search, one per entry."""
    return [n["what"].split()[2].rstrip(":") for n in evaluate(f).needed()
            if n["what"].startswith("search Kataloggruppe")]


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

    def test_catalogue_groups_are_searched_before_positions_are_known(self):
        f = core_facts()
        f.positions, f.materials, f.searched_groups = [], [], []
        f.end_reserves.clear()  # a requirement with usable travel is not complete yet; its group is still searched
        searches = [n for n in evaluate(f).needed() if n["what"].startswith("search Kataloggruppe")]
        groups = sorted(n["what"].split()[2].rstrip(":") for n in searches)
        self.assertEqual(groups, sorted({r.catalogue_group for v in f.revisions if v.current
                                         for r in v.requirements if r.catalogue_group}))
        self.assertTrue(all(n["kind"] == "find" and n["site"] == "ERP" for n in searches))

    def test_a_group_search_lists_its_positions_once_they_are_known(self):
        f = core_facts()
        f.materials = [m for m in f.materials if m.catalogue_group != "G-B"]
        searches = [n for n in evaluate(f).needed() if n["what"].startswith("search Kataloggruppe")]
        self.assertEqual([(n["what"].split()[2], n["positions"]) for n in searches], [("G-B:", ["2"])])

    def test_groups_of_every_revision_are_searched_before_one_is_marked_current(self):
        f = undecided()
        self.assertEqual(sorted(searches(f)), ["G-A", "G-B", "G-OLD"])  # each group once; C is in no position
        text = next(n["what"] for n in evaluate(f).needed() if n["what"].startswith("search Kataloggruppe G-OLD"))
        self.assertIn("stroke 250 mm", text)  # what to match from the latest revision naming the group: R-1, not R-0
        f.positions = []
        self.assertEqual(sorted(searches(f)), ["G-A", "G-B", "G-C", "G-OLD"])

    def test_groups_are_searched_while_the_latest_revision_is_not_read_yet(self):
        f = undecided()
        f.revisions[-1].requirements = []  # R-2 is listed, its requirements are not recorded yet
        self.assertEqual(sorted(searches(f)), ["G-B", "G-OLD"])

    def test_groups_are_searched_while_the_revision_marked_current_is_not_read_yet(self):
        f = undecided()
        f.revisions[-1].current = True  # R-2 is marked current, its requirements are not recorded yet
        f.revisions[-1].requirements = []
        self.assertEqual(sorted(searches(f)), ["G-B", "G-OLD"])

    def test_a_searched_group_is_not_asked_again_before_the_mark(self):
        f = undecided()
        f.searched_groups = ["g-old"]
        self.assertEqual(sorted(searches(f)), ["G-A", "G-B"])
        f = undecided()
        f.materials = [material("M-OLD", 50, 250, 160, "Flansch", "", catalogue_group="G-OLD")]
        self.assertEqual(sorted(searches(f)), ["G-A", "G-B"])

    def test_once_a_revision_is_marked_current_only_its_groups_are_searched(self):
        f = undecided()
        f.revisions[-1].current = True  # R-2
        self.assertEqual(sorted(searches(f)), ["G-A", "G-B"])
        f = undecided()
        f.revisions[1].current = True  # R-1
        self.assertEqual(sorted(searches(f)), ["G-B", "G-OLD"])

    def test_nothing_is_searched_before_revisions_are_recorded(self):
        f = undecided()
        f.revisions = []
        self.assertEqual(searches(f), [])
        self.assertIn("the specification revisions and which one is marked current",
                      [n["what"] for n in evaluate(f).needed()])

    def test_a_search_of_a_group_that_does_not_govern_only_adds_candidates(self):
        f = undecided()
        f.searched_groups = ["G-OLD"]
        f.materials = [material("M-OLD", 50, 250, 160, "Flansch", "", catalogue_group="G-OLD")]
        f.revisions[-1].current = True  # R-2 governs: requirement A is searched in G-A, where nothing is recorded
        result = evaluate(f)
        self.assertEqual(result.answer["decision"], "bid")  # not a search of A's group without a match (rule 3)
        self.assertEqual(result.answer["positions"][0]["material_number"], "")  # nor its nearest candidate
        self.assertIn("G-A", searches(f))
        for v in f.revisions:
            v.current = v.revision == "R-1"  # had R-1 governed, the early search would have found its match
        self.assertEqual(evaluate(f).answer["positions"][0]["material_number"], "M-OLD")

    def test_no_bid_asks_for_a_recheck(self):
        f = core_facts()
        f.materials = [m for m in f.materials if m.material_number != "M-B1"]
        f.searched_groups = ["G-B"]
        result = evaluate(f)
        self.assertEqual(result.answer["decision"], "no_bid")
        self.assertTrue(any(n["kind"] == "verify" and "no_bid" in n["what"] for n in result.needed()))


if __name__ == "__main__":
    unittest.main()
