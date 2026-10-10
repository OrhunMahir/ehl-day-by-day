"""Shared fixture for the engine tests: a small invented tender. Every number here is made up.

Hand calculation of `core_facts()`:
- Position 1: 10 pieces, Q&A Q1 sets 12, Q2 sets 20, Q3 withdraws Q2 -> 12 pieces. Requirement A matches M-A1 (M-A2 is
  inactive, M-A3 has another pressure). Scale 10 reached: 90.00 EUR.
- Position 2: 3 sets of 4 pieces -> 12 pieces. Requirement B matches M-B1. Price per 100 pieces, scale 10 reached:
  24050 / 100 = 240.50 EUR.
- Discount: the latest director approval for the exact reference is 4 % from 3000.00 EUR; the undiscounted order
  value 12 x 90.00 + 12 x 240.50 = 3966.00 reaches it.
- Unit prices 86.40 and 230.88; total 12 x 86.40 + 12 x 230.88 = 3807.36.
- Lead times: M-A1 stock 50 covers 12 -> 1 week; M-B1 stock 5 does not cover 12 -> 8 weeks.
"""
import sys
from datetime import date, datetime, timezone
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from facts import (DiscountApproval, Facts, Material, PriceCondition, QuantityChange, Requirement, Scale,  # noqa: E402
                   Source, SpecRevision, TenderPosition, Unit, Withdrawal)

REF = "T-TEST-1"
DOMAIN = "bidder.test"
DIRECTOR = f"chef@{DOMAIN}"
SCENARIO = datetime(2030, 3, 15, 10, 0, tzinfo=timezone.utc)


def at(day: str, sender: str = "", hour: int = 9) -> Source:
    y, m, d = map(int, day.split("-"))
    return Source(date=datetime(y, m, d, hour, 0, tzinfo=timezone.utc), sender=sender)


def mail(local: str, day: str, hour: int = 9) -> Source:
    return at(day, f"{local}@{DOMAIN}", hour)


def condition(*scales, unit=1, currency="EUR", fx_key="", start=date(2030, 1, 1), end=date(2030, 12, 31)):
    return PriceCondition(valid_from=start, valid_to=end, currency=currency, fx_key=fx_key, price_unit=D(unit),
                          scales=[Scale(D(q), D(p)) for q, p in scales])


def material(number, bore, stroke, pressure, mounting, cert, **kw) -> Material:
    kw.setdefault("active", True)
    return Material(number, bore_mm=D(bore), stroke_mm=D(stroke), pressure_bar=D(pressure), mounting=mounting,
                    certification=cert, **kw)


def core_facts() -> Facts:
    req_a = Requirement("A", D(50), D(300), D(160), "Flansch", "keine gefordert", catalogue_group="G-A")
    req_b = Requirement("B", D(63), D(400), D(200), "Schwenkauge", "EN 9999", catalogue_group="G-B",
                        pieces_per_set=D(4))
    return Facts(
        tender_reference=REF,
        scenario_date=SCENARIO,
        bidder_domain=DOMAIN,
        director_address=DIRECTOR,
        positions=[
            TenderPosition("1", D(10), Unit.PIECE, "A"),
            TenderPosition("2", D(3), Unit.SET, "B"),
        ],
        quantity_changes=[
            QuantityChange("Q1", "1", D(12), at("2030-02-01")),
            QuantityChange("Q2", "1", D(20), at("2030-02-05")),
        ],
        withdrawals=[Withdrawal("Q3", "Q2", at("2030-02-10"))],
        revisions=[
            SpecRevision("R-1", current=False, date=date(2030, 1, 5), requirements=[
                Requirement("A", D(50), D(250), D(160), "Flansch", "keine gefordert"),
                Requirement("B", D(63), D(400), D(200), "Schwenkauge", "EN 9999"),
            ]),
            SpecRevision("R-2", current=True, date=date(2030, 1, 20), requirements=[req_a, req_b]),
        ],
        materials=[
            material("M-A1", 50, 300, 160, "Flansch", "", stock=D(50), stock_lead_weeks=1, production_lead_weeks=6,
                     conditions=[condition((1, "70.00"), start=date(2029, 1, 1), end=date(2029, 12, 31)),
                                 condition((1, "100.00"), (10, "90.00"), (50, "80.00"))]),
            material("M-A2", 50, 300, 160, "Flansch", "", active=False),
            material("M-A3", 50, 300, 170, "Flansch", ""),
            material("M-B1", 63, 400, 200, "schwenkauge", "EN 9999", stock=D(5), stock_lead_weeks=2,
                     production_lead_weeks=8, conditions=[condition((1, "25000"), (10, "24050"), unit=100)]),
        ],
        discount_approvals=[
            DiscountApproval(REF, D(5), source=at("2030-02-01", DIRECTOR)),
            DiscountApproval(REF, D(4), D("3000.00"), at("2030-03-01", DIRECTOR)),
            DiscountApproval(REF + "-B", D(9), source=at("2030-03-10", DIRECTOR)),
            DiscountApproval(REF, D(10), source=at("2030-03-20", DIRECTOR)),
            DiscountApproval(REF, D(9), source=at("2030-03-12", f"vertrieb@{DOMAIN}")),
        ],
    )
