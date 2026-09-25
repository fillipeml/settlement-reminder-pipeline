from decimal import Decimal

from settlement_reminders.formatting import brl
from settlement_reminders.spelled_out import amount_in_words, ordinal_feminine


def test_amount_in_words_without_commas():
    assert (
        amount_in_words(Decimal("2166.66"))
        == "dois mil cento e sessenta e seis reais e sessenta e seis centavos"
    )
    assert "," not in amount_in_words(Decimal("6666.66"))


def test_feminine_ordinal():
    assert ordinal_feminine(1) == "primeira"
    assert ordinal_feminine(2) == "segunda"
    assert ordinal_feminine(3) == "terceira"
    assert ordinal_feminine(6) == "sexta"
    assert ordinal_feminine(7) == "sétima"
    assert ordinal_feminine(21) == "21a"


def test_brl_formatting():
    assert brl(Decimal("1250.5")) == "R$ 1.250,50"
    assert brl(1234567) == "R$ 1.234.567,00"
    assert brl(Decimal("-3.10")) == "R$ -3,10"
