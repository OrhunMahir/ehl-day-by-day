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

import copy
import functools
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

_INVISIBLE = re.compile("[\u200b-\u200f\u2060\ufeff\u00ad]")  # zero-width, direction marks, soft hyphen


@functools.lru_cache(maxsize=65536)
def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _INVISIBLE.sub("", text)).strip()


def clean(text: str) -> str:
    """Remove zero-width characters and collapse whitespace."""
    return _clean(str(text))


@functools.lru_cache(maxsize=65536)
def norm_key(text: str | None) -> str:
    """Canonical form of a code or key: no invisible characters or spaces, upper case."""
    return re.sub(r"\s+", "", clean(text or "")).upper()


@functools.lru_cache(maxsize=65536)
def norm_text(text: str | None) -> str:
    """Canonical form of a free-text attribute such as a mounting type: case-folded, single spaces."""
    return clean(text or "").casefold()


def dec(value, locale: str | None = None, strict: bool = False) -> Decimal:
    """Parse a number to Decimal.

    `locale` is "de" (1.234,56) or "en" (1,234.56); prefixes like "de-DE" or "en-US" work too. Without it the format
    is inferred: with both separators the last one is the decimal mark; a lone comma is a decimal mark; a lone dot
    followed by exactly three digits more than once (1.234.567) is a thousands separator. Units and currency symbols
    around the number are ignored. Ambiguous text such as "1.234" without a locale is read as 1.234, or raises with
    `strict`.
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
    if strict and not loc and re.fullmatch(r"-?[1-9]\d{0,2}[.,]\d{3}", num):
        raise ValueError(f"{value!r} is ambiguous (thousands or decimals?); send a JSON number or state the format")
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
# The tender: positions and what changes them. On the public sites these come from the tender portal's bidder Q&A;
# elsewhere they may sit on another site. `qa_number` is the Q&A answer's number when there is one, else "".


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
    """A clarification that sets a position's quantity, in the position's stated unit (rule 1, 17)."""
    position: str
    quantity: Decimal
    qa_number: str = ""
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
    """A clarification that changes one attribute of one requirement in one named revision (rule 14). For a
    requirement marked as usable travel, a stroke amendment states the usable travel (rule 20)."""
    revision: str
    requirement: str
    attribute: Attr
    value: Decimal | str
    qa_number: str = ""
    source: Source | None = None


class AssignmentKind(str, Enum):
    CONDITION_GROUP = "condition_group"     # Konditionsgruppe (rule 12)
    PACKAGING = "packaging"                 # Verpackungstarif key (rule 23)
    DELIVERY = "delivery"                   # Terminfreigabe / commitment key (rule 24)
    SETUP_POOL = "setup_pool"               # Rüstpool membership (rule 25)
    PROJECT_BONUS = "project_bonus"         # Projektbonus membership (rule 26)


@dataclass
class Assignment:
    """A key assigned to a position (by a Q&A answer, the ERP or wherever the site states it)."""
    position: str
    kind: AssignmentKind
    key: str
    qa_number: str = ""
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


class Allocation(str, Enum):
    """How a fee is split into cents over positions (rule 25: the Rüstpools mail states it). All start with each
    position's floor in cents of its proportional share."""
    LARGEST_REMAINDER = "largest_remainder"         # leftover cents one each to the largest remainders, ties by
                                                    # ascending position id
    LARGEST_REMAINDER_TIES_DESCENDING = "largest_remainder_ties_descending"
    REST_TO_LARGEST_SHARE = "rest_to_largest_share"  # all leftover cents to the position with the largest weight


@dataclass
class SetupPool:
    """Rüstpools: one fee shared by the member positions (rule 25)."""
    reference: str
    key: str
    fee_eur: Decimal
    source: Source | None = None
    allocation: Allocation = Allocation.LARGEST_REMAINDER


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


def to_dict(obj):
    """Plain JSON of a facts dataclass (the reverse of `from_dict`): numbers as text, dates in ISO form."""
    if is_dataclass(obj):
        return {f.name: to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, list):
        return [to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Decimal):
        text = format(obj.normalize(), "f")
        # "1.234" would read as ambiguous; "1.2340" is the same value and is not
        return text + "0" if re.fullmatch(r"-?[1-9]\d{0,2}\.\d{3}", text) else text
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


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
        return dec(value, strict=True)
    if tp is bool:
        if isinstance(value, bool):
            return value
        return norm_text(str(value)) in {"true", "yes", "ja", "1", "aktiv", "active"}
    if tp is int:
        number = dec(value, strict=True)
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


# What a model filling the facts must know beyond the field name.
FIELD_DOCS = {
    "Source.date": "when the record was issued (mail date and time, revision date, Q&A date)",
    "Source.sender": "the sender's full mail address, for mails",
    "Facts.scenario_date": "the scenario date (Stichtag) from the brief or the sites, with time if given",
    "Facts.bidder_domain": "domain of our own mailbox, e.g. the part after @ of the sales inbox",
    "Facts.director_address": "the sales director's mail address as the brief names it",
    "Facts.searched_groups": "every Kataloggruppe searched in the ERP, also searches that found nothing",
    "TenderPosition.quantity": "the tender's quantity in the position's own unit (before any clarification)",
    "TechnicalAmendment.value": "the new value as stated; for a usable-travel requirement the usable travel",
    "Requirement.stroke_mm": "as stated; for a usable-travel requirement the usable travel (reserves are added later)",
    "Requirement.certification": "as stated; null or empty when no certification is required",
    "Requirement.pieces_per_set": "pieces in one Satz/set, when the requirement defines it",
    "Material.active": "true only when the ERP status says the material is active/released",
    "Material.surcharge_mass_kg": "Zuschlagsmasse per piece in kg (625 g = 0.625)",
    "Material.allocation_mass_kg": "Umlagegewicht per piece in kg (3100 g = 3.1)",
    "Material.stock": "pieces in stock",
    "Material.setup_fee_eur": "ordinary setup fee (Rüstkosten) for the whole position, if the ERP states one",
    "PriceCondition.price_unit": "number of pieces the price refers to (price per 100 pieces -> 100)",
    "PriceCondition.fx_key": "the FX key the condition names when its currency is not EUR",
    "Scale.min_quantity": "the scale starts at this many pieces",
    "DiscountApproval.percent": "approved percent; 0 when the message refuses or withdraws the discount",
    "DiscountApproval.min_order_value": "minimum undiscounted net order value in EUR, if the approval has one",
    "IndexWindow.rate_eur_per_kg": "index rate in EUR per kg",
    "DeliveryCommitment.max_quantity": "inclusive ceiling on the total pieces of that material over all positions",
    "BonusBand.percent": "percent for the part of the total between lower and upper",
    "SetupPool.allocation": "the cent-allocation rule the Rüstpools mail states; ask if none of these fits",
}


def json_schema(cls=None) -> dict:
    """JSON Schema of a facts dataclass (default `Facts`) for a model's tool definition."""
    cls = cls or Facts
    hints = typing.get_type_hints(cls)
    props, required = {}, []
    for f in fields(cls):
        props[f.name] = _schema(hints[f.name])
        doc = FIELD_DOCS.get(f"{cls.__name__}.{f.name}")
        if doc:
            props[f.name] = {**props[f.name], "description": doc}
        if f.default is MISSING and f.default_factory is MISSING:
            required.append(f.name)
    schema = {"type": "object", "properties": props, "additionalProperties": False}
    if cls.__doc__ and not cls.__doc__.startswith(cls.__name__ + "("):
        schema["description"] = " ".join(cls.__doc__.split())
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
            out = {"type": ["number", "string"],
                   "description": "a JSON number, or text with a format that is not ambiguous (1.250 is ambiguous)"}
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


# ---------------------------------------------------------------------------------------------------------------------
# What a model filling the facts must know. For the lead's prompt; it states the rules' traps, no values.

FILLING_GUIDE = """How to fill the facts (the engine applies the bid rules; you only record what the sites show):

- tender_reference and scenario_date from the brief or the tender; bidder_domain from our own mailbox address;
  director_address = the sales director's address as the brief names it (without it, approvals from anyone count).
- Record what you read, not what you concluded. Keep superseded, withdrawn, look-alike and later records too, each
  with its own source: the engine picks the one that counts. Do not do any arithmetic yourself.
- Dates with their time and time zone: ISO 8601 with the offset the site states (2030-01-24T17:00:00+01:00) or in
  UTC; a time without a zone is read as UTC.
- source: for every mail value the sender's full address, the mail's date and time, and the exact tender reference
  it names (a reference with an extra suffix is another tender). For Q&A answers their number and date; for
  documents their revision.
- Quantities stay in the unit the tender or Q&A states (sets stay sets); the engine converts with the requirement's
  pieces_per_set. A Q&A answer that withdraws an earlier one goes into withdrawals with that answer's number.
- Requirements come from every revision you saw; mark the one the site marks as current/valid with current=true.
  A technical amendment names the revision and requirement it applies to; record it with that revision even if it
  is not the current one. For usable travel (nutzbarer Weg) enter the stated travel as stroke_mm, set
  usable_travel=true and the end-reserve key; the reserve comes from the matching Technikfreigabe record.
- Materials: every result of the ERP search by Kataloggruppe, with the attributes the result list already shows
  (material number, bore, stroke, pressure, mounting, certification, group); that is enough for candidates that
  do not match. Open the details only of candidates whose attributes match the requirement, and record for them
  the status, stock, lead times, carton size, masses in kg (625 g = 0.625), metal index code, setup fee and every
  price condition with its validity, currency, FX key, price unit and all quantity scales. List every
  Kataloggruppe you searched in searched_groups, also when the search found nothing.
- Keys that tie a position to a rule record (Konditionsgruppe, packaging tariff, delivery commitment, setup pool,
  project bonus) go into assignments, wherever you read them.
- Mail rule records: one record per key per mail. Konditionsgruppe wording -> scope base / base_and_surcharge /
  none. Metal indices with every validity window as stated. Bonus bands as lower/upper/percent. A setup pool's
  allocation = the listed method that matches the mail's wording.
- Discount: every approval-like mail for the tender with its sender, date, percent and any minimum order value; a
  mail that refuses or withdraws the discount is an approval with percent 0.
- Numbers as JSON numbers where you can; text numbers only with an unambiguous format. Unknown = leave it out
  (null); never guess. 0 only when the site states zero.
"""


# ---------------------------------------------------------------------------------------------------------------------
# Building the facts piece by piece, as the reports come in

# Which fields identify an item of each list, so a re-sent item corrects the earlier one instead of adding a copy.
_IDENTITY: dict[str, tuple[str, ...]] = {
    "positions": ("position",),
    "quantity_changes": ("position", "qa_number", "source.date"),
    "withdrawals": ("qa_number", "withdraws"),
    "amendments": ("revision", "requirement", "attribute", "qa_number", "source.date"),
    "assignments": ("position", "kind", "qa_number", "source.date"),
    "revisions": ("revision",),
    "materials": ("material_number",),
    "discount_approvals": ("reference", "source.date", "source.sender"),
    "condition_groups": ("reference", "key", "source.date", "source.sender"),
    "metal_indices": ("reference", "key", "source.date", "source.sender"),
    "packaging_tariffs": ("reference", "key", "source.date", "source.sender"),
    "delivery_commitments": ("reference", "key", "source.date", "source.sender"),
    "fx_rates": ("reference", "key", "source.date", "source.sender"),
    "setup_pools": ("reference", "key", "source.date", "source.sender"),
    "project_bonuses": ("reference", "key", "source.date", "source.sender"),
    "end_reserves": ("reference", "key", "revision", "source.date", "source.sender"),
}
# Items whose fields arrive in parts (a material's attributes, stock and conditions are on different pages).
_FIELD_MERGE = {"positions", "revisions", "materials"}


def _ident(item: dict, paths: tuple[str, ...]) -> tuple:
    out = []
    for path in paths:
        value = item
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value is None or value == "":
            out.append(None)
        elif path.endswith("date"):
            try:
                out.append(when(value).isoformat())
            except ValueError:
                out.append(norm_key(str(value)))
        else:
            out.append(norm_key(str(value)))
    return tuple(out)


def _known(value) -> bool:
    return value not in (None, "", [], {})


def _merge_fields(old: dict, new: dict, prefer_existing: bool = False) -> dict:
    """Fields of `new` override `old`; a null never erases a known value; requirements merge by their id. With
    `prefer_existing`, `new` only fills fields that `old` does not know yet."""
    merged = dict(old)
    for name, value in new.items():
        if value is None:
            continue
        if name == "requirements" and isinstance(value, list) and isinstance(old.get(name), list):
            merged[name] = _merge_list(old[name], value, ("requirement",), True, prefer_existing)[0]
        elif not (prefer_existing and _known(old.get(name))):
            merged[name] = value
    return merged


def _merge_list(old: list, new: list, paths: tuple[str, ...], field_merge: bool,
                prefer_existing: bool = False) -> tuple[list, list[int]]:
    """The merged list (items are never changed in place) and the indexes of the items that are new or changed.
    With `prefer_existing`, a known item is only completed (field merge) or kept as it is (whole-item lists)."""
    items = list(old)
    index = {}
    for i, existing in enumerate(items):
        index.setdefault(_ident(existing, paths), i)
    changed = []
    for item in new:
        if not isinstance(item, dict):
            raise ValueError(f"expected objects in the list, got {item!r}")
        key = _ident(item, paths)
        at = index.get(key)
        if at is None:
            index[key] = len(items)
            changed.append(len(items))
            items.append(copy.deepcopy(item))
        elif field_merge:
            items[at] = _merge_fields(items[at], copy.deepcopy(item), prefer_existing)
            changed.append(at)
        elif not prefer_existing:
            items[at] = copy.deepcopy(item)
            changed.append(at)
    return items, changed


def _item_class(name: str):
    return typing.get_args(typing.get_type_hints(Facts)[name])[0]


class FactsBuilder:
    """Collects the facts as plain JSON over several tool calls.

    `add(update)` merges one update and checks what it changes at once: an update with a bad field is rejected
    whole (ValueError naming the field) and the facts collected so far stay as they were. Lists grow; an item with
    the same identity as an earlier one replaces it (positions, revisions and materials are merged field by field,
    so a material's stock and conditions can arrive after its attributes). `replace_lists` in an update replaces
    whole lists, to take back records added by mistake. `facts()` returns the typed `Facts` for the engine.
    """

    def __init__(self, data: dict | None = None):
        self.data: dict = {}
        if data:
            self.add(data)

    def add(self, update: dict, prefer_existing: bool = False) -> str:
        """Merges one update. With `prefer_existing` (a second source, e.g. a report recorded after the browsing
        agent recorded at the source) the update only adds what is not known yet: it never overwrites a value,
        never replaces a record and never empties a list."""
        if not isinstance(update, dict):
            raise ValueError("the update must be an object")
        update = dict(update)
        replace = set(update.pop("replace_lists", None) or [])
        if prefer_existing:
            replace = set()
        unknown = replace - set(_IDENTITY) - {"searched_groups"}
        if unknown:
            raise ValueError(f"replace_lists: unknown list(s) {', '.join(sorted(unknown))}")
        hints = typing.get_type_hints(Facts)
        names = {f.name for f in fields(Facts)}
        bad = set(update) - names
        if bad:
            raise ValueError(f"Facts: unknown field(s) {', '.join(sorted(bad))}")
        merged = dict(self.data)            # shallow: changed lists are new list objects, items are never mutated
        for name, value in update.items():
            if name in _IDENTITY:
                if value is None:
                    continue
                if not isinstance(value, list):
                    raise ValueError(f"Facts.{name}: expected a list")
                base = [] if name in replace else merged.get(name, [])
                items, changed = _merge_list(base, value, _IDENTITY[name], name in _FIELD_MERGE, prefer_existing)
                cls = _item_class(name)
                for i in changed:
                    try:
                        from_dict(cls, items[i])
                    except ValueError as exc:
                        raise ValueError(f"Facts.{name}: {exc}") from exc
                merged[name] = items
            elif name == "searched_groups":
                groups = [] if name in replace else list(merged.get(name, []))
                seen = {norm_key(x) for x in groups}
                for g in value or []:
                    if norm_key(g) not in seen:
                        seen.add(norm_key(g))
                        groups.append(clean(str(g)))
                merged[name] = groups
            elif value is not None:
                if prefer_existing and _known(merged.get(name)):
                    continue
                new = {**merged.get(name, {}), **value} if name == "authority" and isinstance(value, dict) else value
                try:
                    _convert(hints[name], new)
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"Facts.{name}: {exc}") from exc
                merged[name] = copy.deepcopy(new)
        for name in replace - set(update):
            merged[name] = []
        self.data = merged
        return self.summary()

    def facts(self) -> Facts:
        missing = [n for n in ("tender_reference", "scenario_date") if not self.data.get(n)]
        if missing:
            raise ValueError(f"still missing: {', '.join(missing)}")
        return from_dict(Facts, self.data)

    def summary(self) -> str:
        parts = [f"{n}={self.data[n]}" for n in ("tender_reference", "scenario_date") if self.data.get(n)]
        parts += [f"{n}: {len(v)}" for n, v in self.data.items() if isinstance(v, list) and v]
        return "facts so far: " + ", ".join(parts) if parts else "no facts yet"


def update_schema() -> dict:
    """JSON Schema of one `FactsBuilder.add` update: any part of the facts, plus `replace_lists`."""
    schema = json_schema(Facts)
    schema.pop("required", None)
    schema["properties"]["replace_lists"] = {
        "type": "array", "items": {"type": "string", "enum": sorted(_IDENTITY) + ["searched_groups"]},
        "description": "lists to replace entirely by the ones in this update (to take back wrong records)"}
    schema["description"] = ("Add facts as they are found; send only what is new or corrected. An item with the "
                             "same identity (e.g. the same material_number) updates the earlier one.")
    return schema


def field_reference() -> str:
    """The facts' fields as compact text for a model that records facts while it browses: much shorter than the JSON
    Schema of `update_schema()`. `*` marks a field an item needs; every list item may carry a `source`."""
    def kind(tp) -> str:
        options, _ = _members(tp)
        if datetime in options or date in options:
            return "date"
        if str in options and Decimal in options:
            return "number or text"
        tp = options[0]
        origin = typing.get_origin(tp)
        if origin is list:
            inner = typing.get_args(tp)[0]
            return "list of {" + describe(inner) + "}" if is_dataclass(inner) else "list of " + kind(inner)
        if origin is dict:
            return "object"
        if isinstance(tp, type) and issubclass(tp, Enum):
            return "|".join(m.value for m in tp)
        if is_dataclass(tp):
            return "{" + describe(tp) + "}"
        return {Decimal: "number", bool: "true/false", int: "integer", str: "text"}.get(tp, "text")

    def describe(cls) -> str:
        hints = typing.get_type_hints(cls)
        parts = []
        for f in fields(cls):
            if f.name == "source":
                continue
            mark = "*" if f.default is MISSING and f.default_factory is MISSING else ""
            k = kind(hints[f.name])
            parts.append(f"{f.name}{mark}" + ("" if k == "text" else f": {k}"))
        return ", ".join(parts)

    lines = []
    for f in fields(Facts):
        if f.name == "authority":
            continue
        line = f"- {f.name}: {kind(typing.get_type_hints(Facts)[f.name])}"
        doc = FIELD_DOCS.get(f"Facts.{f.name}")
        lines.append(line + (f" ({doc})" if doc else ""))
    lines.append("- every list item: source: {" + describe(Source) + "} (where it was read; for a message its "
                 "sender's address and date)")
    return "\n".join(lines)
