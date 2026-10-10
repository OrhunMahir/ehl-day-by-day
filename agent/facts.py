"""Typed facts for one tender: the contract between the lead (which fills them from the sub-agents' reports) and the
bid engine (which turns them into the answer object, see `engine.build_bid`).

Every fact carries a `Source`: the website, document or record, date, sender and revision it was read from. The
engine uses dates, senders and references to apply the authority and time rules in code; the rest is for the log.

Conventions:
- Money and measures are `Decimal`. Build them with `dec()`, which also reads German and English number formats.
- Dates are `datetime` (timezone-aware, UTC preferred) or `date`. Build them with `when()`.
- A field that is `None` was not found. The engine never guesses it; it reports what is missing in `BidResult.issues`.
- Keys (Konditionsgruppe, tariff, commitment, pool, bonus, FX, index, end reserve) are compared after `norm_key()`.
"""
from __future__ import annotations

import re
import types
import typing
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum

# ---------------------------------------------------------------------------------------------------------------------
# Provenance


@dataclass(frozen=True)
class Source:
    """Where a fact was read. Only `date` and `sender` change the result; the rest is for traceability."""
    site: str = ""                       # site id from the run's access, e.g. the ERP's siteId
    document: str = ""                   # page, document number, message id or record id
    date: datetime | date | None = None  # when the record was issued (mail date, revision date, Q&A date)
    sender: str = ""                     # mail address of the sender, when it is a message
    revision: str = ""                   # document revision, when it is a versioned document
    note: str = ""                       # free text, e.g. the original wording of the value


# ---------------------------------------------------------------------------------------------------------------------
# Parsing helpers (for whoever fills the facts)

_INVISIBLE = re.compile("[​-‏⁠﻿­]")


def clean(text: str) -> str:
    """Remove zero-width characters and collapse whitespace."""
    return re.sub(r"\s+", " ", _INVISIBLE.sub("", str(text))).strip()


def norm_key(text: str | None) -> str:
    """Canonical form of a code or key: no invisible characters or spaces, upper case."""
    return re.sub(r"\s+", "", clean(text or "")).upper()


def norm_text(text: str | None) -> str:
    """Canonical form of a free-text attribute such as a mounting type: case-folded, single spaces."""
    return clean(text or "").casefold()


def dec(value, locale: str | None = None) -> Decimal:
    """Parse a number to Decimal.

    `locale` is "de" (1.234,56) or "en" (1,234.56); prefixes like "de-DE" or "en-US" work too. Without it the format
    is inferred: with both separators the last one is the decimal mark; a lone comma is a decimal mark; a lone dot
    followed by exactly three digits more than once (1.234.567) is a thousands separator. Units and currency symbols
    around the number are ignored. Ambiguous text such as "1.234" without a locale is read as 1.234.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    text = clean(value).replace("−", "-").replace("'", "").replace(" ", "").replace(" ", "")
    match = re.search(r"-?[\d.,]*\d", text)
    if not match:
        raise ValueError(f"no number in {value!r}")
    num = match.group(0)
    loc = (locale or "").lower()[:2]
    if loc == "de":
        num = num.replace(".", "").replace(",", ".")
    elif loc == "en":
        num = num.replace(",", "")
    elif "," in num and "." in num:
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        if num.count(",") > 1:
            num = num.replace(",", "")
        else:
            num = num.replace(",", ".")
    elif num.count(".") > 1:
        num = num.replace(".", "")
    try:
        return Decimal(num)
    except InvalidOperation as exc:
        raise ValueError(f"cannot read {value!r} as a number") from exc


_MASS_FACTORS = {"kg": Decimal(1), "g": Decimal("0.001"), "t": Decimal(1000), "mg": Decimal("0.000001")}


def mass_kg(text, unit_name: str | None = None) -> Decimal:
    """A mass in kg from text such as '625 g/Stück' or '0,625 kg'; `unit_name` overrides the unit in the text."""
    found = re.search(r"\b(mg|kg|g|t)\b", clean(unit_name or str(text)).lower())
    if not found:
        raise ValueError(f"no mass unit in {text!r}")
    return dec(text) * _MASS_FACTORS[found.group(1)]


def when(value) -> datetime | date:
    """Parse a date or timestamp. ISO forms (2030-02-01, 2030-02-01T10:00:00Z) and German dates (01.02.2030,
    01.02.2030 10:00) are read; a timestamp without a zone is taken as UTC."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return value
    text = clean(value)
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?:,?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?)?(?:\s*(?:UTC|Z))?", text)
    if m:
        d, mo, y, hh, mm, ss = m.groups()
        if hh is None:
            return date(int(y), int(mo), int(d))
        return datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss or 0), tzinfo=timezone.utc)
    iso = text.replace(" UTC", "Z").replace("Z", "+00:00")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso):
        return date.fromisoformat(iso)
    parsed = datetime.fromisoformat(iso)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def mail_address(text: str | None) -> str:
    """The bare address from 'Name <local@domain>' or 'local@domain', lower case; '' if there is none."""
    m = re.search(r"[\w.+-]+@[\w.-]+", clean(text or ""))
    return m.group(0).lower() if m else ""


class Unit(str, Enum):
    PIECE = "piece"
    SET = "set"


def unit(text: str | None) -> Unit | None:
    """Read a quantity unit as shown on a site (Stück, Stk, ST, Satz, ...)."""
    t = norm_text(text).rstrip(".")
    if t in {"stück", "stk", "st", "stck", "pcs", "pc", "piece", "pieces", "stueck"}:
        return Unit.PIECE
    if t in {"satz", "sätze", "saetze", "set", "sets"}:
        return Unit.SET
    return None


# ---------------------------------------------------------------------------------------------------------------------
# Tender portal: positions and bidder Q&A


@dataclass
class TenderPosition:
    """One line of the bill of quantities."""
    position: str                       # position number as shown; the scorer matches on it
    quantity: Decimal                   # in `unit`
    unit: Unit = Unit.PIECE
    requirement: str = ""               # the specification requirement it refers to
    source: Source | None = None


@dataclass
class QuantityChange:
    """A Q&A answer that sets a position's quantity, in the position's stated unit (rule 1, 17)."""
    qa_number: str
    position: str
    quantity: Decimal
    source: Source | None = None


@dataclass
class Withdrawal:
    """A Q&A answer that withdraws (zieht zurück) an earlier answer by its number (rule 13)."""
    qa_number: str
    withdraws: str                      # the number of the withdrawn answer
    source: Source | None = None


class Attr(str, Enum):
    """Technical attributes that decide the material (rule 3)."""
    BORE = "bore_mm"
    STROKE = "stroke_mm"
    PRESSURE = "pressure_bar"
    MOUNTING = "mounting"
    CERTIFICATION = "certification"


@dataclass
class TechnicalAmendment:
    """A Q&A answer that changes one attribute of one requirement in one named revision (rule 14). For a requirement
    marked as usable travel, a stroke amendment states the usable travel (rule 20)."""
    qa_number: str
    revision: str
    requirement: str
    attribute: Attr
    value: Decimal | str
    source: Source | None = None


class AssignmentKind(str, Enum):
    CONDITION_GROUP = "condition_group"     # Konditionsgruppe (rule 12)
    PACKAGING = "packaging"                 # Verpackungstarif key (rule 23)
    DELIVERY = "delivery"                   # Terminfreigabe / commitment key (rule 24)
    SETUP_POOL = "setup_pool"               # Rüstpool membership (rule 25)
    PROJECT_BONUS = "project_bonus"         # Projektbonus membership (rule 26)


@dataclass
class Assignment:
    """A Q&A answer that assigns a key to a position."""
    qa_number: str
    position: str
    kind: AssignmentKind
    key: str
    source: Source | None = None


# ---------------------------------------------------------------------------------------------------------------------
# Supplier portal: specification


@dataclass
class Requirement:
    """One requirement as stated in one specification revision."""
    requirement: str
    bore_mm: Decimal | None = None
    stroke_mm: Decimal | None = None
    pressure_bar: Decimal | None = None
    mounting: str | None = None
    certification: str | None = None    # "keine gefordert" and the like are read as no certification
    catalogue_group: str = ""           # Kataloggruppe, the ERP search key (rule 10)
    pieces_per_set: Decimal | None = None   # when the requirement defines a Satz (rule 17)
    usable_travel: bool = False         # stroke is "nutzbarer Weg" (rule 20)
    end_reserve_key: str = ""           # key of the per-end reserve in the Technikfreigabe (rule 20)


@dataclass
class SpecRevision:
    revision: str
    current: bool | None = None         # marked as the valid/current revision on the site
    date: datetime | date | None = None
    requirements: list[Requirement] = field(default_factory=list)
    source: Source | None = None


# ---------------------------------------------------------------------------------------------------------------------
# ERP: materials, stock, price conditions


@dataclass
class Scale:
    """Quantity scale of a price condition: from `min_quantity` (in the condition's scale unit, normally pieces) the
    price per `PriceCondition.price_unit` pieces is `price`."""
    min_quantity: Decimal
    price: Decimal


@dataclass
class PriceCondition:
    valid_from: datetime | date | None
    valid_to: datetime | date | None
    currency: str = "EUR"
    fx_key: str = ""                    # key in the Devisenfixing mail when the currency is not EUR (rule 22)
    price_unit: Decimal = Decimal(1)    # the price is per this many pieces
    scales: list[Scale] = field(default_factory=list)
    source: Source | None = None


@dataclass
class Material:
    material_number: str
    active: bool | None = None
    catalogue_group: str = ""
    bore_mm: Decimal | None = None
    stroke_mm: Decimal | None = None
    pressure_bar: Decimal | None = None
    mounting: str | None = None
    certification: str | None = None
    stock: Decimal | None = None                    # pieces available
    stock_lead_weeks: int | None = None             # lead time when delivered from stock
    production_lead_weeks: int | None = None
    pieces_per_carton: Decimal | None = None        # Packinhalt (rule 23)
    allocation_mass_kg: Decimal | None = None       # Umlagegewicht per piece, weight for setup pools (rule 25)
    surcharge_mass_kg: Decimal | None = None        # Zuschlagsmasse per piece, for the metal surcharge (rule 28)
    metal_index: str = ""                           # Preisindex code, empty when the material has none
    setup_fee_eur: Decimal | None = None            # ordinary setup fee, once per position (rule 27)
    conditions: list[PriceCondition] = field(default_factory=list)
    source: Source | None = None


# ---------------------------------------------------------------------------------------------------------------------
# Webmail: approvals and keyed rule records. One record = one key read from one message.


@dataclass
class DiscountApproval:
    """The sales director's approval for a tender (rule 5, 16). `percent` 0 records a withdrawal or refusal."""
    reference: str
    percent: Decimal
    min_order_value: Decimal | None = None   # minimum undiscounted net order value, inclusive
    source: Source | None = None


class DiscountScope(str, Enum):
    """What a Konditionsgruppe lets the discount apply to (rule 12). Setup fees are never discounted."""
    BASE = "base"
    BASE_AND_SURCHARGE = "base_and_surcharge"
    NONE = "none"


@dataclass
class ConditionGroupRule:
    """Konditionsregeln: one group key and what the discount applies to."""
    reference: str
    key: str
    scope: DiscountScope
    source: Source | None = None


@dataclass
class IndexWindow:
    rate_eur_per_kg: Decimal
    valid_from: date | None = None      # inclusive; None = open
    valid_to: date | None = None        # inclusive; None = open


@dataclass
class MetalIndex:
    """Preisindizes: one index code with its rates (rule 28)."""
    reference: str
    key: str
    windows: list[IndexWindow]
    source: Source | None = None


@dataclass
class PackagingTariff:
    """Verpackungstarife: EUR per started carton (rule 23)."""
    reference: str
    key: str
    eur_per_carton: Decimal
    source: Source | None = None


@dataclass
class DeliveryCommitment:
    """Terminfreigabe: lead time for a material when pooled demand stays within a ceiling (rule 24)."""
    reference: str
    key: str
    material_number: str
    max_quantity: Decimal               # inclusive ceiling on the pooled final pieces of that material
    lead_weeks: int
    source: Source | None = None


@dataclass
class FxRate:
    """Devisenfixing: EUR per one unit of a currency (rule 22)."""
    reference: str
    key: str
    currency: str
    eur_per_unit: Decimal
    source: Source | None = None


@dataclass
class SetupPool:
    """Rüstpools: one fee shared by the member positions (rule 25)."""
    reference: str
    key: str
    fee_eur: Decimal
    source: Source | None = None


@dataclass
class BonusBand:
    """Marginal band: `percent` applies to the part of the goods total between `lower` and `upper` (None = open)."""
    lower: Decimal
    upper: Decimal | None
    percent: Decimal


@dataclass
class ProjectBonus:
    """Projektbonus: marginal bands over the eligible undiscounted goods total (rule 26)."""
    reference: str
    key: str
    bands: list[BonusBand]
    source: Source | None = None


@dataclass
class EndReserve:
    """Technikfreigabe: per-end reserve for one key in one specification revision (rule 20)."""
    reference: str
    key: str
    revision: str
    reserve_mm: Decimal
    source: Source | None = None


# Who may issue which record (rule 21): mailbox local part at the bidder's domain. These come from the bid rules in
# the brief, not from the sites; the lead may replace them from the run's brief.
DEFAULT_AUTHORITY: dict[str, str] = {
    "EndReserve": "technik",
    "PackagingTariff": "logistik",
    "DeliveryCommitment": "logistik",
    "FxRate": "finanzen",
    "ProjectBonus": "finanzen",
    "SetupPool": "kalkulation",
    "ConditionGroupRule": "kalkulation",
    "MetalIndex": "kalkulation",
}


# ---------------------------------------------------------------------------------------------------------------------
# Everything for one bid


@dataclass
class Facts:
    tender_reference: str
    scenario_date: datetime | date
    bidder_domain: str = ""             # domain of our own mailbox; senders are checked against it
    director_address: str = ""          # the sales director's mailbox, from the brief
    authority: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_AUTHORITY))

    positions: list[TenderPosition] = field(default_factory=list)
    quantity_changes: list[QuantityChange] = field(default_factory=list)
    withdrawals: list[Withdrawal] = field(default_factory=list)
    amendments: list[TechnicalAmendment] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)

    revisions: list[SpecRevision] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    # Kataloggruppen searched in the ERP, also when the search found nothing. Only a searched requirement without a
    # matching material makes the bid a no_bid; otherwise the material counts as not yet found.
    searched_groups: list[str] = field(default_factory=list)

    discount_approvals: list[DiscountApproval] = field(default_factory=list)
    condition_groups: list[ConditionGroupRule] = field(default_factory=list)
    metal_indices: list[MetalIndex] = field(default_factory=list)
    packaging_tariffs: list[PackagingTariff] = field(default_factory=list)
    delivery_commitments: list[DeliveryCommitment] = field(default_factory=list)
    fx_rates: list[FxRate] = field(default_factory=list)
    setup_pools: list[SetupPool] = field(default_factory=list)
    project_bonuses: list[ProjectBonus] = field(default_factory=list)
    end_reserves: list[EndReserve] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------------------------------
# Plain JSON in and out, so a model can fill the facts through one tool call

def from_dict(cls, data: dict):
    """Build a facts dataclass (normally `Facts`) from plain JSON: numbers may be strings in German or English
    format, dates may be ISO or German, enums are given by value. Unknown fields raise, so a typo is not lost."""
    if not isinstance(data, dict):
        raise ValueError(f"{cls.__name__}: expected an object, got {type(data).__name__}")
    hints = typing.get_type_hints(cls)
    names = {f.name for f in fields(cls)}
    unknown = set(data) - names
    if unknown:
        raise ValueError(f"{cls.__name__}: unknown field(s) {', '.join(sorted(unknown))}")
    kwargs = {}
    for name, value in data.items():
        try:
            kwargs[name] = _convert(hints[name], value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{cls.__name__}.{name}: {exc}") from exc
    return cls(**kwargs)


def _members(tp) -> tuple[list, bool]:
    if typing.get_origin(tp) in (typing.Union, types.UnionType):
        args = typing.get_args(tp)
        return [a for a in args if a is not type(None)], type(None) in args
    return [tp], False


def _convert(tp, value):
    options, optional = _members(tp)
    if value is None or (optional and value == ""):
        if optional:
            return None
        raise ValueError("value required")
    if datetime in options or date in options:
        return when(value)
    if str in options and Decimal in options:          # an amended attribute: number or text
        return value if isinstance(value, str) else dec(value)
    tp = options[0]
    origin = typing.get_origin(tp)
    if origin is list:
        if not isinstance(value, list):
            raise ValueError("expected a list")
        return [_convert(typing.get_args(tp)[0], v) for v in value]
    if origin is dict:
        return {str(k): _convert(typing.get_args(tp)[1], v) for k, v in value.items()}
    if tp is Decimal:
        return dec(value)
    if tp is bool:
        if isinstance(value, bool):
            return value
        return norm_text(str(value)) in {"true", "yes", "ja", "1", "aktiv", "active"}
    if tp is int:
        number = dec(value)
        if number != number.to_integral_value():
            raise ValueError(f"{value!r} is not a whole number")
        return int(number)
    if tp is str:
        return clean(str(value))
    if tp is Unit:
        found = unit(value) if not isinstance(value, Unit) else value
        if found is None:
            raise ValueError(f"unknown unit {value!r}")
        return found
    if isinstance(tp, type) and issubclass(tp, Enum):
        try:
            return tp(value)
        except ValueError:
            return tp[str(value).upper()]
    if is_dataclass(tp):
        return from_dict(tp, value)
    raise TypeError(f"unsupported type {tp}")


def json_schema(cls=None) -> dict:
    """JSON Schema of a facts dataclass (default `Facts`) for a model's tool definition."""
    cls = cls or Facts
    hints = typing.get_type_hints(cls)
    props, required = {}, []
    for f in fields(cls):
        props[f.name] = _schema(hints[f.name])
        if f.default is MISSING and f.default_factory is MISSING:
            required.append(f.name)
    schema = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def _schema(tp) -> dict:
    options, optional = _members(tp)
    if datetime in options or date in options:
        out = {"type": "string", "description": "date or timestamp, ISO 8601 (UTC) or DD.MM.YYYY"}
    elif str in options and Decimal in options:
        out = {"type": ["string", "number"]}
    else:
        tp = options[0]
        origin = typing.get_origin(tp)
        if origin is list:
            out = {"type": "array", "items": _schema(typing.get_args(tp)[0])}
        elif origin is dict:
            out = {"type": "object", "additionalProperties": _schema(typing.get_args(tp)[1])}
        elif tp is Decimal:
            out = {"type": ["number", "string"]}
        elif tp is bool:
            out = {"type": "boolean"}
        elif tp is int:
            out = {"type": "integer"}
        elif tp is str:
            out = {"type": "string"}
        elif isinstance(tp, type) and issubclass(tp, Enum):
            out = {"type": "string", "enum": [m.value for m in tp]}
        elif is_dataclass(tp):
            out = json_schema(tp)
        else:
            raise TypeError(f"unsupported type {tp}")
    if optional:
        out = {"anyOf": [out, {"type": "null"}]}
    return out
