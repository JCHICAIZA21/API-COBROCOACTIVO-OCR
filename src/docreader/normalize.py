"""Canonical form of field values, so extracted and expected values compare reliably."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from .domain import FieldKind

_SEPARATORS = re.compile(r"[\s.,-]+")
_ID_NUMBER = re.compile(r"\d{3,25}", re.ASCII)

_ISO_DATE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})", re.ASCII)
_NUMERIC_DATE = re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})", re.ASCII)
_NAMED_DATE = re.compile(
    r"(\d{1,2})\s*(?:de\s+|[/.\s-])\s*([a-z]+)\.?\s*(?:del?\s+|[/.\s-])\s*(\d{4}|\d{2})",
    re.IGNORECASE | re.ASCII,
)
_MONTH_NAMES = (
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre"
).split()
_MONTHS = {
    alias: number
    for number, name in enumerate(_MONTH_NAMES, start=1)
    for alias in (name, name[:3])
} | {"sept": 9, "set": 9, "setiembre": 9}

_MONEY = re.compile(r"\d+(?:[.,]\d+)*", re.ASCII)

_PLATE_NOISE = re.compile(r"[\s-]+")
_PLATE = re.compile(r"[A-Z]{3}\d{2,3}[A-Z]?", re.ASCII)


def _text(raw: str) -> str | None:
    return " ".join(raw.split()) or None


def _digits(raw: str) -> str | None:
    digits = _SEPARATORS.sub("", raw)
    return digits if digits.isascii() and digits.isdigit() else None


def _id_number(raw: str) -> str | None:
    digits = _digits(raw)
    return digits if digits and _ID_NUMBER.fullmatch(digits) else None


def _integer(raw: str) -> str | None:
    digits = _digits(raw)
    return str(int(digits)) if digits else None


def _iso(year: str, month: int | None, day: str) -> str | None:
    if month is None:
        return None
    full_year = int(year) + 2000 if len(year) == 2 else int(year)
    try:
        return date(full_year, month, int(day)).isoformat()
    except ValueError:
        return None


def _date(raw: str) -> str | None:
    """Day-first dates (Colombia), numeric or with Spanish month names, to ISO `YYYY-MM-DD`."""
    value = " ".join(raw.split())
    if match := _ISO_DATE.fullmatch(value):
        year, month, day = match.groups()
        return _iso(year, int(month), day)
    if match := _NUMERIC_DATE.fullmatch(value):
        day, month, year = match.groups()
        return _iso(year, int(month), day)
    if match := _NAMED_DATE.fullmatch(value):
        day, month_name, year = match.groups()
        return _iso(year, _MONTHS.get(month_name.lower()), day)
    return None


def _money(raw: str) -> str | None:
    """Colombian format first. With both separators the last one is decimal; with one kind,
    it is thousands when every group after the first has 3 digits, else decimal."""
    value = "".join(raw.replace("$", "").split())
    if not _MONEY.fullmatch(value):
        return None
    separators = {char for char in value if char in ".,"}
    fraction = ""
    if len(separators) == 2:
        decimal = value[max(value.rfind("."), value.rfind(","))]
        integer, _, fraction = value.rpartition(decimal)
        if decimal in integer:
            return None
        groups = integer.split("," if decimal == "." else ".")
    elif separators:
        groups = value.split(separators.pop())
        if len(groups) == 2 and len(groups[1]) != 3:
            fraction = groups.pop()
    else:
        groups = [value]
    if any(len(group) != 3 for group in groups[1:]):
        return None
    whole = str(int("".join(groups)))
    fraction = fraction.rstrip("0")
    return f"{whole}.{fraction}" if fraction else whole


def _plate(raw: str) -> str | None:
    plate = _PLATE_NOISE.sub("", raw).upper()
    return plate if _PLATE.fullmatch(plate) else None


_NORMALIZERS: dict[str, Callable[[str], str | None]] = {
    "text": _text,
    "id_number": _id_number,
    "integer": _integer,
    "date": _date,
    "money": _money,
    "plate": _plate,
}


def normalize(kind: FieldKind, raw: str | None) -> str | None:
    """Return the canonical string for `raw` as a `kind` value, or None if empty or invalid."""
    if raw is None:
        return None
    return _NORMALIZERS[kind](raw)
