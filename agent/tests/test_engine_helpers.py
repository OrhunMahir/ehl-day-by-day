"""Parsing helpers in facts.py and money helpers in engine/money.py."""
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal as D

import test_engine_base  # noqa: F401  (puts agent/ on the path)

from engine.money import allocate_cents, half_up, position_key
from facts import Unit, dec, mail_address, norm_key, unit, when


class Numbers(unittest.TestCase):
    def test_formats(self):
        cases = {
            "1.234,56": D("1234.56"), "1,234.56": D("1234.56"), "3,15 EUR/kg": D("3.15"), "625 g": D("625"),
            "1.234.567": D("1234567"), "12.5": D("12.5"), "-4,5 %": D("-4.5"), "1 234,5": D("1234.5"),
        }
        for text, want in cases.items():
            self.assertEqual(dec(text), want, text)

    def test_locale_hint(self):
        self.assertEqual(dec("1.234", "de-DE"), D("1234"))
        self.assertEqual(dec("1,234", "en-US"), D("1234"))
        self.assertEqual(dec("1,234", "de"), D("1.234"))

    def test_zero_width_characters(self):
        self.assertEqual(dec("2​0​0"), D(200))
        self.assertEqual(norm_key("K​-​1 "), "K-1")

    def test_rejects_text_without_number(self):
        with self.assertRaises(ValueError):
            dec("keine")


class Dates(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(when("2030-02-01"), date(2030, 2, 1))
        self.assertEqual(when("01.02.2030"), date(2030, 2, 1))
        self.assertEqual(when("01.02.2030, 10:30:00 UTC"), datetime(2030, 2, 1, 10, 30, tzinfo=timezone.utc))
        self.assertEqual(when("2030-02-01T10:30:00Z"), datetime(2030, 2, 1, 10, 30, tzinfo=timezone.utc))


class Misc(unittest.TestCase):
    def test_units(self):
        self.assertEqual([unit(t) for t in ("Stück", "ST", "Satz", "Sätze", "kg")],
                         [Unit.PIECE, Unit.PIECE, Unit.SET, Unit.SET, None])

    def test_mail_address(self):
        self.assertEqual(mail_address("Kalkulation <Kalkulation@Bidder.Test>"), "kalkulation@bidder.test")
        self.assertEqual(mail_address(""), "")


class Money(unittest.TestCase):
    def test_half_up(self):
        self.assertEqual(half_up(D("2.345")), D("2.35"))
        self.assertEqual(half_up(D("2.3449999")), D("2.34"))
        self.assertEqual(half_up(D("-2.345")), D("-2.35"))

    def test_position_order(self):
        self.assertEqual(sorted(["10", "9", "2", "1.10", "1.2"], key=position_key), ["1.2", "1.10", "2", "9", "10"])

    def test_allocation_floors_then_largest_remainders(self):
        # 10.00 over weights 1:1:1 -> 3.33 each, 1 cent left, equal remainders -> lowest position id ("2")
        self.assertEqual(allocate_cents(D("10.00"), {"10": D(1), "9": D(1), "2": D(1)}),
                         {"10": D("3.33"), "9": D("3.33"), "2": D("3.34")})

    def test_allocation_by_remainder_size(self):
        # 1.00 over 1:2 -> 33.33 / 66.67 cents -> 33 / 66 floor, the cent goes to the larger remainder (0.67)
        self.assertEqual(allocate_cents(D("1.00"), {"1": D(1), "2": D(2)}), {"1": D("0.33"), "2": D("0.67")})

    def test_allocation_ties_descending(self):
        self.assertEqual(allocate_cents(D("10.00"), {"10": D(1), "9": D(1), "2": D(1)},
                                        "largest_remainder_ties_descending"),
                         {"10": D("3.34"), "9": D("3.33"), "2": D("3.33")})

    def test_allocation_rest_to_largest_share(self):
        # 1.00 over 1:1:2 -> 25 / 25 / 50 cents exactly; 0.10 over 1:1:1 -> 3 / 3 / 3 and 1 cent to the first largest
        self.assertEqual(allocate_cents(D("0.10"), {"3": D(1), "1": D(1), "2": D(1)}, "rest_to_largest_share"),
                         {"3": D("0.03"), "1": D("0.04"), "2": D("0.03")})
        self.assertEqual(allocate_cents(D("0.11"), {"1": D(1), "2": D(3)}, "rest_to_largest_share"),
                         {"1": D("0.02"), "2": D("0.09")})

    def test_unknown_allocation_method(self):
        with self.assertRaises(ValueError):
            allocate_cents(D("1.00"), {"1": D(1)}, "something_else")

    def test_allocation_sums_to_total(self):
        shares = allocate_cents(D("517.29"), {str(i): D(i * 7 + 3) for i in range(1, 8)})
        self.assertEqual(sum(shares.values()), D("517.29"))


class Json(unittest.TestCase):
    """facts.from_dict and facts.json_schema: what a model fills through one tool call."""

    DATA = {
        "tender_reference": "T-TEST-1",
        "scenario_date": "2030-03-15T10:00:00Z",
        "bidder_domain": "bidder.test",
        "director_address": "chef@bidder.test",
        "positions": [{"position": "1", "quantity": "10", "unit": "Stück", "requirement": "A"},
                      {"position": "2", "quantity": 3, "unit": "Satz", "requirement": "B"}],
        "quantity_changes": [{"qa_number": "Q1", "position": "1", "quantity": "12",
                              "source": {"site": "tender", "date": "01.02.2030"}}],
        "amendments": [{"qa_number": "Q2", "revision": "R-2", "requirement": "A", "attribute": "stroke_mm",
                        "value": "300"}],
        "assignments": [{"qa_number": "Q3", "position": "1", "kind": "packaging", "key": "VP-1"}],
        "revisions": [{"revision": "R-2", "current": True, "date": "2030-01-20", "requirements": [
            {"requirement": "A", "bore_mm": "50", "stroke_mm": "300", "pressure_bar": "160", "mounting": "Flansch",
             "certification": "keine gefordert", "catalogue_group": "G-A"}]}],
        "materials": [{"material_number": "M-A1", "active": "aktiv", "catalogue_group": "G-A", "bore_mm": 50,
                       "stroke_mm": "300,0", "pressure_bar": "160", "mounting": "Flansch", "stock": "1250",
                       "pieces_per_carton": "2,5", "stock_lead_weeks": "1",
                       "conditions": [{"valid_from": "01.01.2030, 00:00:00 UTC", "valid_to": "31.12.2030",
                                       "price_unit": "1", "scales": [{"min_quantity": "1", "price": "1.234,50"}]}]}],
        "discount_approvals": [{"reference": "T-TEST-1", "percent": "4", "min_order_value": "3.000,00",
                                "source": {"date": "2030-03-01T09:00:00Z", "sender": "Chef <chef@bidder.test>"}}],
        "metal_indices": [{"reference": "T-TEST-1", "key": "IX-1", "windows": [
            {"rate_eur_per_kg": "3,15", "valid_from": "2030-03-01", "valid_to": None}]}],
    }

    def test_from_dict_converts_types(self):
        from datetime import datetime, timezone
        from facts import AssignmentKind, Attr, Facts, from_dict
        f = from_dict(Facts, self.DATA)
        self.assertEqual(f.scenario_date, datetime(2030, 3, 15, 10, tzinfo=timezone.utc))
        self.assertEqual((f.positions[0].quantity, f.positions[1].unit), (D(10), Unit.SET))
        self.assertEqual(f.quantity_changes[0].source.date, date(2030, 2, 1))
        self.assertEqual((f.amendments[0].attribute, f.amendments[0].value), (Attr.STROKE, "300"))
        self.assertEqual(f.assignments[0].kind, AssignmentKind.PACKAGING)
        m = f.materials[0]
        self.assertEqual((m.active, m.stroke_mm, m.stock, m.stock_lead_weeks), (True, D("300.0"), D(1250), 1))
        self.assertEqual(m.pieces_per_carton, D("2.5"))
        self.assertEqual(m.conditions[0].scales[0].price, D("1234.50"))
        self.assertEqual(f.discount_approvals[0].min_order_value, D(3000))
        self.assertIsNone(f.metal_indices[0].windows[0].valid_to)

    def test_from_dict_result_prices(self):
        from engine import build_bid
        from facts import Facts, from_dict
        answer = build_bid(from_dict(Facts, {**self.DATA, "positions": self.DATA["positions"][:1]}))
        self.assertEqual(answer["positions"][0]["base_unit_price"], 1234.5)
        self.assertEqual(answer["positions"][0]["quantity"], 12)

    def test_unknown_field_rejected(self):
        from facts import Facts, from_dict
        with self.assertRaises(ValueError) as ctx:
            from_dict(Facts, {**self.DATA, "tender_ref": "x"})
        self.assertIn("tender_ref", str(ctx.exception))

    def test_bad_value_names_the_field(self):
        from facts import Facts, from_dict
        bad = {**self.DATA, "positions": [{"position": "1", "quantity": "zehn", "requirement": "A"}]}
        with self.assertRaises(ValueError) as ctx:
            from_dict(Facts, bad)
        self.assertIn("quantity", str(ctx.exception))

    def test_ambiguous_number_text_rejected(self):
        from facts import Facts, from_dict
        for text in ("1.250", "1,250", "-12.500"):
            bad = {**self.DATA, "materials": [{"material_number": "M", "stock": text}]}
            with self.assertRaises(ValueError, msg=text) as ctx:
                from_dict(Facts, bad)
            self.assertIn("Material.stock", str(ctx.exception))
        ok = {**self.DATA, "materials": [{"material_number": "M", "stock": 1.25, "pieces_per_carton": "0,250"}]}
        m = from_dict(Facts, ok).materials[0]
        self.assertEqual((m.stock, m.pieces_per_carton), (D("1.25"), D("0.250")))

    def test_schema_covers_every_field(self):
        import json
        from facts import Facts, json_schema
        schema = json_schema()
        self.assertEqual(schema["required"], ["tender_reference", "scenario_date"])
        self.assertEqual(set(schema["properties"]), {f for f in Facts.__dataclass_fields__})
        material = schema["properties"]["materials"]["items"]
        self.assertFalse(material["additionalProperties"])
        self.assertIn("conditions", material["properties"])
        json.dumps(schema)

    def test_filling_guide_names_real_fields(self):
        import re
        from facts import FILLING_GUIDE, json_schema
        names = set()

        def collect(schema):
            for name, sub in schema.get("properties", {}).items():
                names.add(name)
                for part in (sub, sub.get("items", {}), *sub.get("anyOf", [])):
                    if isinstance(part, dict):
                        collect(part.get("items", part) if part.get("type") == "array" else part)
        collect(json_schema())
        used = set(re.findall(r"\b[a-z]+_[a-z_]+\b", FILLING_GUIDE)) - {"base_and_surcharge"}
        self.assertTrue(used, "no field names found")
        self.assertEqual(used - names, set())

    def test_mass(self):
        from facts import mass_kg
        self.assertEqual(mass_kg("625 g/Stück"), D("0.625"))
        self.assertEqual(mass_kg("0,7 kg"), D("0.7"))
        self.assertEqual(mass_kg("3100", "g"), D("3.1"))
        with self.assertRaises(ValueError):
            mass_kg("625")


if __name__ == "__main__":
    unittest.main()
