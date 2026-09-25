"""Amounts and ordinals spelled out in Brazilian Portuguese (the client-facing e-mails)."""

from __future__ import annotations

from decimal import Decimal

from num2words import num2words

# Feminine ordinals ("parcela" is feminine): 1 -> primeira, 2 -> segunda...
_ORDINALS_FEMININE = {
    1: "primeira",
    2: "segunda",
    3: "terceira",
    4: "quarta",
    5: "quinta",
    6: "sexta",
    7: "sétima",
    8: "oitava",
    9: "nona",
    10: "décima",
    11: "décima primeira",
    12: "décima segunda",
    13: "décima terceira",
    14: "décima quarta",
    15: "décima quinta",
    16: "décima sexta",
    17: "décima sétima",
    18: "décima oitava",
    19: "décima nona",
    20: "vigésima",
}


def amount_in_words(value: Decimal | float) -> str:
    """2166.66 -> 'dois mil cento e sessenta e seis reais e sessenta e seis centavos'.

    num2words inserts commas ('dois mil, cento e...'); the firm's standard does not use
    them, so they are removed.
    """
    text = num2words(float(value), lang="pt_BR", to="currency")
    return text.replace(",", "")


def ordinal_feminine(number: int) -> str:
    """Feminine ordinal spelled out, e.g. 2 -> 'segunda'."""
    return _ORDINALS_FEMININE.get(number, f"{number}a")
