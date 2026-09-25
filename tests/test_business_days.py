from datetime import date

from settlement_reminders.business_days import (
    business_days_before,
    is_business_day,
    next_business_day,
    weekday_name,
)


def test_weekday_name_is_portuguese():
    assert weekday_name(date(2026, 7, 15)) == "quarta-feira"
    assert weekday_name(date(2026, 8, 15)) == "sábado"


def test_next_business_day_skips_the_weekend():
    # 15/08/2026 is a Saturday -> next business day 17/08 (Monday)
    assert is_business_day(date(2026, 8, 15)) is False
    assert next_business_day(date(2026, 8, 15)) == date(2026, 8, 17)


def test_ordinary_business_day():
    assert is_business_day(date(2026, 7, 15)) is True
    assert next_business_day(date(2026, 7, 15)) == date(2026, 7, 15)


def test_business_days_before_skips_the_weekend():
    # Monday 15/06/2026: 3 business days before -> Fri 12, Thu 11, Wed 10/06
    assert business_days_before(date(2026, 6, 15), 3) == date(2026, 6, 10)
    assert business_days_before(date(2026, 6, 15), 0) == date(2026, 6, 15)


def test_business_days_before_skips_a_holiday():
    # 07/09/2026 (Independence Day) falls on a Monday; 1 business day before Tuesday 08/09
    # is Friday 04/09
    assert business_days_before(date(2026, 9, 8), 1) == date(2026, 9, 4)
