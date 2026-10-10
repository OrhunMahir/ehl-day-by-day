"""The plain-text explanation of a bid (engine/explain.py)."""
import unittest

from test_engine_base import core_facts
from test_engine_random import SEEDS, invent

from engine import evaluate


class Explain(unittest.TestCase):
    def test_names_the_deciding_facts(self):
        text = evaluate(core_facts()).explain()
        for part in ("discount 4 %", "approval 4 %", "order value 3966.00: met", "revision R-2 (marked current)",
                     "Q2 2030-02-05: 20 (withdrawn)", "x 4 pieces per set = 12 pieces", "material M-A1",
                     "M-A2 (inactive)", "M-A3 (pressure differ)", "scale from 10", "unit price 86.40",
                     "1 dated after the scenario date", "1 not from the sales director", "1 other tender reference"):
            self.assertIn(part, text)

    def test_no_bid_and_missing_facts_do_not_break_it(self):
        f = core_facts()
        f.materials = [m for m in f.materials if m.material_number != "M-B1"]
        f.searched_groups = ["G-B"]
        self.assertIn("no_bid", evaluate(f).explain())
        f = core_facts()
        f.revisions = []
        self.assertIn("not found", evaluate(f).explain())

    def test_every_random_tender(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                result = evaluate(invent(seed).facts)
                text = result.explain()
                for p in result.answer["positions"]:
                    self.assertIn(f"Position {p['position']}:", text)


if __name__ == "__main__":
    unittest.main()
