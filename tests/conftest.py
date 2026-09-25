"""pytest configuration: neutralises the developer's environment and pins the clock."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

# The tests must NEVER inherit the developer's real configuration (a mailbox, an API key,
# Graph credentials): environment variables take precedence over the .env file, so they are
# neutralised here. Without this, a filled production .env would make the flow tests sweep
# the REAL mailbox and call the API for real.
for _var in (
    "INGEST_MAILBOX",
    "ANTHROPIC_API_KEY",
    "MS_TENANT_ID",
    "MS_CLIENT_ID",
    "MS_CLIENT_SECRET",
    "ALERT_EMAILS",
    "CLIENT_DIRECTORY_PATH",
    "INGEST_ALLOWED_SENDERS",
    "MS_SENDER",
    "CONTROLLERSHIP_EMAILS",
    "CC_NOTICES",
    "CC_BY_PAYER",
    "DEMO_MODE",
    "REFERENCE_DATE",
):
    os.environ[_var] = ""

# the collection cycle parameters: the tests assume the code defaults (interval 2, cap 5)
os.environ["COLLECTION_INTERVAL_BUSINESS_DAYS"] = "2"
os.environ["COLLECTION_MAX_ATTEMPTS"] = "5"
os.environ["HOLIDAY_SUBDIVISION"] = "GO"
os.environ["DRY_RUN"] = "true"


def freeze_clock(monkeypatch, today: date) -> None:
    """Makes the history record `sent_at` on the simulated day, not on the real day.

    The daily collection lock (`collected_today`) compares the record's date with the
    routine's 'today'. In the tests 'today' is simulated, but the record used the real
    clock: on the day the real date coincided with a fixed date of a test, the lock fired
    by mistake and three tests failed.
    """
    import settlement_reminders.history as history_mod

    monkeypatch.setattr(
        history_mod,
        "now_utc",
        lambda: datetime(today.year, today.month, today.day, 12, 0, tzinfo=UTC),
    )
