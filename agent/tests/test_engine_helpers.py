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

    def test_allocation_sums_to_total(self):
        shares = allocate_cents(D("517.29"), {str(i): D(i * 7 + 3) for i in range(1, 8)})
        self.assertEqual(sum(shares.values()), D("517.29"))


if __name__ == "__main__":
    unittest.main()
