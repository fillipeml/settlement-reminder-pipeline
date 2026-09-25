"""Sending rules: the preventive reminder and the post-due collection cycle.

- Preventive reminder: `days_before` BUSINESS days before the DUE DATE (the original due
  date moved to the next business day), one per installment.
- Collection: an installment overdue without a receipt-based settlement is collected every
  `interval` business days, up to `cap` attempts; when the schedule is exhausted without a
  settlement the installment ESCALATES to a human and the automation stops collecting it.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from settlement_reminders.business_days import (
    business_days_after,
    business_days_before,
    is_business_day,
    next_business_day,
)
from settlement_reminders.models import Installment


def today_in_tz(timezone: str, override: date | None = None) -> date:
    """'Today' in the configured timezone, or the pinned reference date."""
    if override is not None:
        return override
    return datetime.now(ZoneInfo(timezone)).date()


def due_date(installment: Installment) -> date:
    """Payment due date (the original due date moved to a business day)."""
    return next_business_day(installment.original_due)


def reminder_day(installment: Installment, days_before: int) -> date:
    """The day the reminder of this installment must go out."""
    return business_days_before(due_date(installment), days_before)


def should_send(
    installment: Installment, today: date, days_before: int, catchup_days: int = 0
) -> bool:
    """True when today is the reminder day, or inside the recovery window.

    `catchup_days` tolerates missed runs (the computer was off on the target day): the
    reminder still goes out up to N calendar days later, as long as today is a business day
    and the due date has not passed. The history (idempotency) guarantees nothing is sent
    twice.
    """
    target = reminder_day(installment, days_before)
    if today == target:
        return True
    if catchup_days <= 0:
        return False
    return (
        is_business_day(today)
        and target < today <= due_date(installment)
        and (today - target).days <= catchup_days
    )


def select_installments(
    installments: Iterable[Installment], today: date, days_before: int, catchup_days: int = 0
) -> list[Installment]:
    """Filters the installments that must receive a reminder today."""
    return [i for i in installments if should_send(i, today, days_before, catchup_days)]


# -------------------------- post-due collection cycle ---------------------------------


def collections_due(
    installment: Installment, today: date, *, interval: int = 2, cap: int = 5
) -> int:
    """How many collections should have gone out by today (0..cap).

    The schedule starts on the 1st business day AFTER the due date and repeats every
    `interval` business days: with interval=2 and cap=5, the attempts fall on the 1st, 3rd,
    5th, 7th and 9th business day after the due date (about two weeks). Comparing
    'sent < due' makes the cycle self-healing: a missed run is caught up on the next one,
    one collection per day, never duplicated.
    """
    n = business_days_after(due_date(installment), today)
    if n < 1:
        return 0
    return min(cap, (n - 1) // interval + 1)


def schedule_exhausted(
    installment: Installment, today: date, *, interval: int = 2, cap: int = 5
) -> bool:
    """True once the window of the last collection has passed (time to escalate)."""
    return business_days_after(due_date(installment), today) >= 1 + cap * interval
