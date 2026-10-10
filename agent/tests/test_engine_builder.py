"""FactsBuilder: facts sent in pieces, in any order, give the same bid as the facts sent at once."""
import random
import unittest
from decimal import Decimal as D

from test_engine_base import core_facts
from test_engine_random import invent

from engine import evaluate
from facts import FactsBuilder, to_dict, update_schema


def pieces(data: dict, rng: random.Random) -> list[dict]:
    """Cut plain facts into random updates: list items spread over updates, materials and revisions split field by
    field (each part keeps the identity), every update in random order."""
    updates = [dict() for _ in range(rng.randint(2, 6))]
    for name, value in data.items():
        if not isinstance(value, list):
            rng.choice(updates)[name] = value
            continue
        for item in value:
            parts = [item]
            if name == "materials":
                keys = [k for k in item if k != "material_number"]
                rng.shuffle(keys)
                cut = rng.randint(0, len(keys))
                parts = [{"material_number": item["material_number"], **{k: item[k] for k in keys[:cut]}},
                         {"material_number": item["material_number"], **{k: item[k] for k in keys[cut:]}}]
            elif name == "revisions" and isinstance(item, dict) and item.get("requirements"):
                reqs = list(item["requirements"])
                cut = rng.randint(0, len(reqs))
                parts = [{**item, "requirements": reqs[:cut]},
                         {"revision": item["revision"], "requirements": reqs[cut:]}]
            for part in parts:
                rng.choice(updates).setdefault(name, []).append(part)
    rng.shuffle(updates)
    return updates


class Builder(unittest.TestCase):
    def test_round_trip_of_core_facts(self):
        b = FactsBuilder()
        b.add(to_dict(core_facts()))
        self.assertEqual(evaluate(b.facts()).answer, evaluate(core_facts()).answer)

    def test_random_tenders_in_pieces(self):
        for seed in range(0, 300, 6):
            with self.subTest(seed=seed):
                facts = invent(seed).facts
                b = FactsBuilder()
                for update in pieces(to_dict(facts), random.Random(seed)):
                    b.add(update)
                self.assertEqual(evaluate(b.facts()).answer, evaluate(facts).answer)

    def test_material_fields_arrive_in_parts(self):
        b = FactsBuilder()
        b.add({"materials": [{"material_number": "M-1", "bore_mm": 50, "stock": 7}]})
        b.add({"materials": [{"material_number": "M-1", "stock": 9, "pressure_bar": None}]})
        self.assertEqual(b.data["materials"], [{"material_number": "M-1", "bore_mm": 50, "stock": 9}])

    def test_same_identity_replaces_also_with_other_date_format(self):
        b = FactsBuilder()
        mail = {"reference": "T-1", "key": "VP-1", "source": {"date": "2030-03-01T09:00:00Z", "sender": "a@b.test"}}
        b.add({"packaging_tariffs": [{**mail, "eur_per_carton": 5}]})
        b.add({"packaging_tariffs": [{**mail, "eur_per_carton": 6,
                                      "source": {"date": "01.03.2030 09:00", "sender": "A@B.test"}}]})
        self.assertEqual([t["eur_per_carton"] for t in b.data["packaging_tariffs"]], [6])

    def test_other_date_is_another_record(self):
        b = FactsBuilder()
        mail = {"reference": "T-1", "key": "VP-1", "eur_per_carton": 5}
        b.add({"packaging_tariffs": [{**mail, "source": {"date": "2030-03-01"}}]})
        b.add({"packaging_tariffs": [{**mail, "source": {"date": "2030-03-02"}}]})
        self.assertEqual(len(b.data["packaging_tariffs"]), 2)

    def test_bad_update_rejected_whole_and_state_kept(self):
        b = FactsBuilder()
        b.add({"tender_reference": "T-1", "positions": [{"position": "1", "quantity": 3}]})
        before = b.data
        with self.assertRaises(ValueError) as ctx:
            b.add({"positions": [{"position": "2", "quantity": 4}], "materials": [{"material_number": "M",
                                                                                    "stock": "viele"}]})
        self.assertIn("Material.stock", str(ctx.exception))
        self.assertIs(b.data, before)
        self.assertEqual(len(b.data["positions"]), 1)

    def test_replace_lists_takes_back_records(self):
        b = FactsBuilder()
        b.add({"discount_approvals": [{"reference": "T-1-X", "percent": 9}]})
        b.add({"replace_lists": ["discount_approvals"],
               "discount_approvals": [{"reference": "T-1", "percent": 4}]})
        self.assertEqual(b.data["discount_approvals"], [{"reference": "T-1", "percent": 4}])
        b.add({"replace_lists": ["discount_approvals"]})
        self.assertEqual(b.data["discount_approvals"], [])
        with self.assertRaises(ValueError):
            b.add({"replace_lists": ["nonsense"]})

    def test_searched_groups_union(self):
        b = FactsBuilder()
        b.add({"searched_groups": ["G-1"]})
        b.add({"searched_groups": ["g-1", "G-2"]})
        self.assertEqual(b.data["searched_groups"], ["G-1", "G-2"])

    def test_authority_merges(self):
        b = FactsBuilder()
        b.add({"authority": {"FxRate": "treasury"}})
        b.add({"authority": {"SetupPool": "plant"}})
        self.assertEqual(b.data["authority"], {"FxRate": "treasury", "SetupPool": "plant"})

    def test_facts_need_reference_and_date(self):
        b = FactsBuilder()
        b.add({"positions": [{"position": "1", "quantity": 1}]})
        with self.assertRaises(ValueError) as ctx:
            b.facts()
        self.assertIn("tender_reference", str(ctx.exception))
        b.add({"tender_reference": "T-1", "scenario_date": "2030-03-15"})
        self.assertEqual(b.facts().positions[0].quantity, D(1))

    def test_update_schema(self):
        schema = update_schema()
        self.assertNotIn("required", schema)
        self.assertIn("replace_lists", schema["properties"])
        self.assertIn("materials", schema["properties"])


if __name__ == "__main__":
    unittest.main()
