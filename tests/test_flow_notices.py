"""End-to-end flow with the 'notices' source: store -> rules -> sending -> history."""

from __future__ import annotations

from datetime import date

from settlement_reminders.config import Settings
from settlement_reminders.history import SendHistory
from settlement_reminders.ingest import AgreementStore
from settlement_reminders.mailer import EmailSender
from tests.conftest import freeze_clock
from tests.factories import make_extracted_agreement


class _FakeSender(EmailSender):
    def __init__(self):
        self.sent = []

    def send(self, *, to, subject, html, cc=None, inline_images=None):
        self.sent.append({"to": to, "cc": cc or [], "subject": subject, "html": html})


def _execute(monkeypatch, settings, sender, history, today):
    import settlement_reminders.run as run_mod

    monkeypatch.setattr(run_mod, "today_in_tz", lambda tz, override=None: today)
    freeze_clock(monkeypatch, today)
    return run_mod.execute(settings, sender, history)


def _settings(tmp_path) -> Settings:
    return Settings(
        dry_run=True,
        days_before_due=3,
        sources="notices",
        ms_sender="reminders@lawfirm.example",
        history_db=str(tmp_path / "data.sqlite"),
    )


def test_registered_agreement_gets_a_reminder_on_the_right_day(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = AgreementStore(settings.history_db)
    store.upsert(
        make_extracted_agreement(),
        client_emails=["finance@example.com"],
        match_status="ok",
        match_info="exact",
        status="active",
        issues=[],
    )
    history = SendHistory(settings.history_db)
    sender = _FakeSender()

    # installment 1 is due 14/06/2026 (Sunday) -> due date 15/06 (Monday);
    # 3 BUSINESS days before: Fri 12, Thu 11, Wed 10/06
    summary = _execute(monkeypatch, settings, sender, history, date(2026, 6, 10))

    assert summary.total_agreements == 1
    assert summary.total_installments == 7
    assert summary.selected == 1
    assert summary.simulated == 1
    env = sender.sent[0]
    assert env["to"] == ["finance@example.com"]
    assert "1ª PARCELA" in env["subject"]
    assert env["cc"] == []  # no controllership configured; the lawyer is not in Cc
    assert "15/06/2026 (segunda-feira)" in env["html"]
    assert "prorrogado para o primeiro dia útil" in env["html"]

    # idempotency: the same run on the same day does not resend
    r2 = _execute(monkeypatch, settings, _FakeSender(), history, date(2026, 6, 10))
    assert r2.simulated == 0
    assert r2.already_sent == 1


def test_shadow_simulation_does_not_consume_the_real_send(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = AgreementStore(settings.history_db)
    store.upsert(
        make_extracted_agreement(),
        client_emails=["finance@example.com"],
        match_status="ok",
        match_info="exact",
        status="active",
        issues=[],
    )
    history = SendHistory(settings.history_db)
    today = date(2026, 6, 10)

    # the reminder day in shadow mode (DRY_RUN): simulates
    r1 = _execute(monkeypatch, settings, _FakeSender(), history, today)
    assert r1.simulated == 1

    # same day, shadow again: does not re-simulate (DRY_RUN idempotency)
    r2 = _execute(monkeypatch, settings, _FakeSender(), history, today)
    assert r2.already_sent == 1

    # go-live on the same day: the simulation does NOT block the real send
    live = _settings(tmp_path)
    live.dry_run = False
    sender = _FakeSender()
    r3 = _execute(monkeypatch, live, sender, history, today)
    assert r3.sent == 1
    assert len(sender.sent) == 1

    # and the real send blocks new attempts (real or simulated)
    r4 = _execute(monkeypatch, live, _FakeSender(), history, today)
    assert r4.already_sent == 1
    r5 = _execute(monkeypatch, settings, _FakeSender(), history, today)
    assert r5.already_sent == 1


def test_closed_agreement_sends_nothing(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = AgreementStore(settings.history_db)
    extracted = make_extracted_agreement()
    store.upsert(
        extracted,
        client_emails=["finance@example.com"],
        match_status="ok",
        match_info="exact",
        status="active",
        issues=[],
    )
    store.close(extracted.case_number, "the client sent a receipt")
    sender = _FakeSender()

    summary = _execute(
        monkeypatch, settings, sender, SendHistory(settings.history_db), date(2026, 6, 10)
    )

    assert summary.total_installments == 0
    assert sender.sent == []


def test_last_installment_uses_the_obligations_beneficiary(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = AgreementStore(settings.history_db)
    store.upsert(
        make_extracted_agreement(),
        client_emails=["finance@example.com"],
        match_status="ok",
        match_info="exact",
        status="active",
        issues=[],
    )
    sender = _FakeSender()

    # installment 7 is due 14/12/2026 (Monday, a business day);
    # 3 BUSINESS days before: Fri 11, Thu 10, Wed 09/12
    summary = _execute(
        monkeypatch, settings, sender, SendHistory(settings.history_db), date(2026, 12, 9)
    )

    assert summary.selected == 1
    env = sender.sent[0]
    assert "7ª PARCELA" in env["subject"]
    assert "sétima e última parcela" in env["html"]
    assert "R$ 4.800,00" in env["html"]
    assert "BELTRANO PROCURADOR SOUZA" in env["html"]
