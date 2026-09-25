"""Date helpers in the Brazilian context: weekday names, business days and public holidays."""

from __future__ import annotations

from datetime import date, timedelta

import holidays

WEEKDAYS_PT = {
    0: "segunda-feira",
    1: "terça-feira",
    2: "quarta-feira",
    3: "quinta-feira",
    4: "sexta-feira",
    5: "sábado",
    6: "domingo",
}

DEFAULT_SUBDIVISION = "GO"
_calendar: holidays.HolidayBase = holidays.Brazil(subdiv=DEFAULT_SUBDIVISION)


def use_subdivision(code: str) -> None:
    """Switches the holiday calendar to national + the given state (ISO code, e.g. 'SP')."""
    global _calendar
    _calendar = holidays.Brazil(subdiv=code) if code else holidays.Brazil()


def weekday_name(d: date) -> str:
    """Name of the weekday in Portuguese (the e-mails to the client are in Portuguese)."""
    return WEEKDAYS_PT[d.weekday()]


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _calendar


def next_business_day(d: date) -> date:
    """`d` itself when it is a business day; otherwise the next one."""
    current = d
    while not is_business_day(current):
        current += timedelta(days=1)
    return current


def business_days_after(start: date, end: date) -> int:
    """How many business days there are in the interval (start, end]. 0 if end <= start."""
    if end <= start:
        return 0
    n = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if is_business_day(current):
            n += 1
    return n


def business_days_before(d: date, n: int) -> date:
    """The n-th business day BEFORE `d` (n=1 -> the previous business day; n=0 -> `d`)."""
    current = d
    for _ in range(n):
        current -= timedelta(days=1)
        while not is_business_day(current):
            current -= timedelta(days=1)
    return current
