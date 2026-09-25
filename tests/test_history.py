from datetime import date

from settlement_reminders.history import SendHistory


def test_idempotency_avoids_a_duplicate(tmp_path):
    history = SendHistory(str(tmp_path / "h.sqlite"))
    due = date(2026, 6, 18)

    assert history.already_sent("P001", due) is False

    history.record(
        external_id="P001",
        due=due,
        case_number="AGR-1",
        client_email="maria@example.com",
        source="excel",
        status="sent",
    )
    assert history.already_sent("P001", due) is True


def test_a_failure_does_not_mark_as_sent(tmp_path):
    history = SendHistory(str(tmp_path / "h.sqlite"))
    due = date(2026, 6, 18)

    history.record(
        external_id="P009",
        due=due,
        case_number="AGR-9",
        client_email="x@example.com",
        source="excel",
        status="failed",
        error="timeout",
    )
    # a failure must not block a new send
    assert history.already_sent("P009", due) is False


def test_real_sends_lists_sent_and_failed_only(tmp_path):
    history = SendHistory(str(tmp_path / "h.sqlite"))
    due = date(2026, 6, 18)
    history.record(
        external_id="P1",
        due=due,
        case_number="A",
        client_email="x",
        source="reminder",
        status="simulated",
    )
    history.record(
        external_id="P2",
        due=due,
        case_number="A",
        client_email="x",
        source="reminder",
        status="sent",
    )
    history.record(
        external_id="P3",
        due=due,
        case_number="A",
        client_email="x",
        source="reminder",
        status="failed",
        error="boom",
    )
    assert [r["external_id"] for r in history.real_sends()] == ["P2", "P3"]
