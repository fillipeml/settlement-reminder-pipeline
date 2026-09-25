"""Brazilian formatting of amounts and dates."""

from __future__ import annotations

from datetime import date
from decimal import Decimal


def brl(value: Decimal | float | int) -> str:
    """Brazilian currency format: 1250.5 -> 'R$ 1.250,50'."""
    value = Decimal(str(value)).quantize(Decimal("0.01"))
    integer, _, cents = f"{value:.2f}".partition(".")
    negative = integer.startswith("-")
    integer = integer.lstrip("-")
    parts = []
    while len(integer) > 3:
        parts.insert(0, integer[-3:])
        integer = integer[:-3]
    parts.insert(0, integer)
    sign = "-" if negative else ""
    return f"R$ {sign}{'.'.join(parts)},{cents}"


def date_br(d: date) -> str:
    """dd/mm/yyyy."""
    return d.strftime("%d/%m/%Y")
