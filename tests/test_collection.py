"""The post-due collection cycle: the rule, the run's pass and the escalation."""

from __future__ import annotations

from datetime import date

from settlement_reminders.config import Settings
from settlement_reminders.history import SendHistory
from settlement_reminders.ingest import AgreementStore
from settlement_reminders.mailer import EmailSender
from settlement_reminders.rules import collections_due, schedule_exhausted
from tests.conftest import freeze_clock
from tests.factories import CASE_OTHER, make_agreement


class _FakeSender(EmailSender):
    def __init__(self):
        self.sent = []

    def send(self, *, to, subject, html, cc=None, inline_images=None):
        self.sent.append({"to": to, "cc": cc or [], "subject": subject, "html": html})


class _FakeSource:
    def __init__(self, agreements):
        self._a = agreements

    def fetch_agreements(self):
        return self._a


def _run(monkeypatch, settings, agreements, sender, history, today):
    import settlement_reminders.run as run_mod

    monkeypatch.setattr(run_mod, "build_sources", lambda s: [_FakeSource(agreements)])
    monkeypatch.setattr(run_mod, "today_in_tz", lambda tz, override=None: today)
    freeze_clock(monkeypatch, today)
    return run_mod.execute(settings, sender, history)


def _settings(tmp_path, **over) -> Settings:
    data = dict(
        dry_run=True,
        days_before_due=3,
        controllership_emails="controllership@lawfirm.example",
        alert_emails="operator@lawfirm.example",
        history_db=str(tmp_path / "h.sqlite"),
    )
    data.update(over)
    return Settings(**data)


# An agreement with one installment due 16/09/2026 (Wednesday, a business day).
# Business days after the due date: 17(1) 18(2) 21(3) 22(4) 23(5) 24(6) 25(7) 28(8) 29(9)
# 30(10) 01/10(11). Collections due: 17, 21, 23, 25, 29; the schedule is exhausted on 01/10.
_DUE = date(2026, 9, 16)


def _overdue_agreement():
    return make_agreement(
        client_emails="client@x.com",
        lawyer_email="lawyer@lawfirm.example",
        installment_count=1,
        first_due=_DUE,
    )


# ------------------------------------------------------------------- rule ----


def test_collections_due_follows_the_schedule():
    installment = _overdue_agreement().installments()[0]
    cases = {
        date(2026, 9, 16): 0,  # the due date: no collection yet
        date(2026, 9, 17): 1,  # 1st business day after
        date(2026, 9, 18): 1,
        date(2026, 9, 19): 1,  # Saturday does not advance the schedule
        date(2026, 9, 21): 2,
        date(2026, 9, 23): 3,
        date(2026, 9, 25): 4,
        date(2026, 9, 29): 5,
        date(2026, 10, 20): 5,  # the cap
    }
    for today, expected in cases.items():
        assert collections_due(installment, today) == expected, today


def test_schedule_exhausted_on_the_11th_business_day():
    installment = _overdue_agreement().installments()[0]
    assert not schedule_exhausted(installment, date(2026, 9, 30))
    assert schedule_exhausted(installment, date(2026, 10, 1))


# ------------------------------------------------------------------- pass ----


def test_collection_after_the_due_date_is_one_per_day(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    history = SendHistory(settings.history_db)
    agreement = _overdue_agreement()

    # the due date: no collection
    r0 = _run(monkeypatch, settings, [agreement], _FakeSender(), history, _DUE)
    assert r0.collections_simulated == 0

    # 1st business day after: attempt 1
    s1 = _FakeSender()
    r1 = _run(monkeypatch, settings, [agreement], s1, history, date(2026, 9, 17))
    assert r1.collections_simulated == 1
    (env,) = s1.sent
    assert env["to"] == ["client@x.com"]
    assert env["cc"] == ["controllership@lawfirm.example"]  # the lawyer stays out
    assert env["subject"].startswith("PAGAMENTO EM ABERTO - 1ª PARCELA")
    assert "encaminhe o comprovante" in env["html"]

    # a repeated run on the SAME day: nothing (one collection per installment per day)
    r1b = _run(monkeypatch, settings, [agreement], _FakeSender(), history, date(2026, 9, 17))
    assert r1b.collections_simulated == 0

    # the next day (not a collection day of the schedule): nothing
    r2 = _run(monkeypatch, settings, [agreement], _FakeSender(), history, date(2026, 9, 18))
    assert r2.collections_simulated == 0

    # 3rd business day after: attempt 2
    r3 = _run(monkeypatch, settings, [agreement], _FakeSender(), history, date(2026, 9, 21))
    assert r3.collections_simulated == 1


def test_catchup_replays_attempts_one_per_day(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    history = SendHistory(settings.history_db)
    agreement = _overdue_agreement()

    # the machine stayed off until 23/09 (3 attempts due): replays 1
    r1 = _run(monkeypatch, settings, [agreement], _FakeSender(), history, date(2026, 9, 23))
    assert r1.collections_simulated == 1
    # the next day: replays the 2nd
    r2 = _run(monkeypatch, settings, [agreement], _FakeSender(), history, date(2026, 9, 24))
    assert r2.collections_simulated == 1


def test_gentle_ramp_one_collection_per_agreement_per_day(monkeypatch, tmp_path):
    # a debtor with 3 overdue installments gets NO burst: the collections flow one per day
    settings = _settings(tmp_path)
    history = SendHistory(settings.history_db)
    agreement = make_agreement(
        client_emails="client@x.com",
        lawyer_email="lawyer@lawfirm.example",
        installment_count=3,
        first_due=date(2026, 6, 5),
    )
    # the installments are due 05/06, 05/07, 05/08: all overdue in September

    s1 = _FakeSender()
    r1 = _run(monkeypatch, settings, [agreement], s1, history, date(2026, 9, 16))
    assert r1.collections_simulated == 1
    assert "1ª PARCELA" in s1.sent[0]["subject"]

    s2 = _FakeSender()
    r2 = _run(monkeypatch, settings, [agreement], s2, history, date(2026, 9, 17))
    assert r2.collections_simulated == 1
    assert "2ª PARCELA" in s2.sent[0]["subject"]

    s3 = _FakeSender()
    r3 = _run(monkeypatch, settings, [agreement], s3, history, date(2026, 9, 18))
    assert r3.collections_simulated == 1
    assert "3ª PARCELA" in s3.sent[0]["subject"]


def test_cc_extra_of_the_agreement_enters_the_copies(monkeypatch, tmp_path):
    # the lawyer indicated extra copies (the notice's CC_EXTRA): they join the controllership
    settings = _settings(tmp_path)
    history = SendHistory(settings.history_db)
    agreement = make_agreement(
        client_emails="client@x.com",
        lawyer_email="lawyer@lawfirm.example",
        cc_emails="coordinator@lawfirm.example",
        installment_count=1,
        first_due=_DUE,
    )

    s = _FakeSender()
    _run(monkeypatch, settings, [agreement], s, history, date(2026, 9, 17))
    (env,) = s.sent
    assert env["cc"] == ["controllership@lawfirm.example", "coordinator@lawfirm.example"]


def test_emails_to_the_client_carry_the_institutional_frame(monkeypatch, tmp_path):
    settings = _settings(tmp_path, firm_name="Example Law Firm")
    history = SendHistory(settings.history_db)

    s = _FakeSender()
    _run(monkeypatch, settings, [_overdue_agreement()], s, history, date(2026, 9, 17))
    (env,) = s.sent
    assert "cid:brand-mark" in env["html"]  # the header with the mark
    assert "Example Law Firm" in env["html"]
    assert "Atenciosamente" in env["html"]  # the signature by department
    assert "Controladoria Jurídica" in env["html"]
    assert "Conteúdo confidencial" in env["html"]  # the standard footer


def test_settled_installment_is_not_collected(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    history = SendHistory(settings.history_db)
    agreement = _overdue_agreement()
    installment = agreement.installments()[0]
    AgreementStore(settings.history_db).settle_installment(
        installment.external_id, agreement.case_number, "receipt", "test"
    )

    r = _run(monkeypatch, settings, [agreement], _FakeSender(), history, date(2026, 9, 17))
    assert r.collections_simulated == 0


def test_escalation_after_the_cap_goes_to_the_lawyer_once(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    history = SendHistory(settings.history_db)
    agreement = _overdue_agreement()

    # sends the 5 attempts on the schedule days (1st, 3rd, 5th, 7th, 9th business day)
    days = [
        date(2026, 9, 17),
        date(2026, 9, 21),
        date(2026, 9, 23),
        date(2026, 9, 25),
        date(2026, 9, 29),
    ]
    for d in days:
        r = _run(monkeypatch, settings, [agreement], _FakeSender(), history, d)
        assert r.collections_simulated == 1, d

    # the schedule has not been exhausted yet: no escalation
    mid = _run(monkeypatch, settings, [agreement], _FakeSender(), history, date(2026, 9, 30))
    assert mid.escalated == []

    # the 11th business day after the due date: escalates to the lawyer, Cc controllership
    s = _FakeSender()
    r = _run(monkeypatch, settings, [agreement], s, history, date(2026, 10, 1))
    assert len(r.escalated) == 1
    (env,) = s.sent
    assert env["to"] == ["lawyer@lawfirm.example"]
    assert env["cc"] == ["controllership@lawfirm.example"]
    assert "ESCALATION" in env["subject"]
    assert "stopped collecting" in env["html"]

    # idempotent: the next day neither escalates again nor collects
    r2 = _run(monkeypatch, settings, [agreement], _FakeSender(), history, date(2026, 10, 2))
    assert r2.escalated == []
    assert r2.collections_simulated == 0


def test_escalation_without_a_lawyer_falls_to_the_alert_list(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    history = SendHistory(settings.history_db)
    agreement = make_agreement(
        client_emails="client@x.com", lawyer_email=None, installment_count=1, first_due=_DUE
    )
    for d in [
        date(2026, 9, 17),
        date(2026, 9, 21),
        date(2026, 9, 23),
        date(2026, 9, 25),
        date(2026, 9, 29),
    ]:
        _run(monkeypatch, settings, [agreement], _FakeSender(), history, d)

    s = _FakeSender()
    r = _run(monkeypatch, settings, [agreement], s, history, date(2026, 10, 1))
    assert len(r.escalated) == 1
    assert s.sent[0]["to"] == ["operator@lawfirm.example"]


def test_a_simulated_collection_does_not_consume_the_real_send(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    history = SendHistory(settings.history_db)
    agreement = _overdue_agreement()

    r1 = _run(monkeypatch, settings, [agreement], _FakeSender(), history, date(2026, 9, 17))
    assert r1.collections_simulated == 1

    live = _settings(tmp_path, dry_run=False)
    s = _FakeSender()
    r2 = _run(monkeypatch, live, [agreement], s, history, date(2026, 9, 17))
    assert r2.collections_sent == 1  # the simulation did not block the real send


def test_cap_zero_disables_collection(monkeypatch, tmp_path):
    settings = _settings(tmp_path, collection_max_attempts=0)
    history = SendHistory(settings.history_db)
    r = _run(
        monkeypatch, settings, [_overdue_agreement()], _FakeSender(), history, date(2026, 9, 17)
    )
    assert r.collections_simulated == 0
    assert r.escalated == []


# ------------- guards: never collect from someone who may have paid -----------------


def test_persisted_hold_blocks_collection_and_a_settlement_releases_it(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    history = SendHistory(settings.history_db)
    agreement = _overdue_agreement()
    store = AgreementStore(settings.history_db)
    store.hold_collection(agreement.case_number, "receipt without a matching installment")

    s = _FakeSender()
    r = _run(monkeypatch, settings, [agreement], s, history, date(2026, 9, 17))
    assert r.collections_simulated == 0
    assert s.sent == []
    assert len(r.collections_held) == 1 and agreement.case_number in r.collections_held[0]

    # a human reconciled and released: the collection returns, replaying the attempt due
    store.release_collection(agreement.case_number)
    r2 = _run(monkeypatch, settings, [agreement], _FakeSender(), history, date(2026, 9, 18))
    assert r2.collections_simulated == 1
    assert r2.collections_held == []


def test_hold_also_blocks_the_escalation(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    history = SendHistory(settings.history_db)
    agreement = _overdue_agreement()
    for d in [
        date(2026, 9, 17),
        date(2026, 9, 21),
        date(2026, 9, 23),
        date(2026, 9, 25),
        date(2026, 9, 29),
    ]:
        assert (
            _run(
                monkeypatch, settings, [agreement], _FakeSender(), history, d
            ).collections_simulated
            == 1
        )

    AgreementStore(settings.history_db).hold_collection(
        agreement.case_number, "an unread .heic attachment"
    )
    s = _FakeSender()
    r = _run(monkeypatch, settings, [agreement], s, history, date(2026, 10, 1))
    assert r.escalated == []
    assert s.sent == []
    assert len(r.collections_held) == 1


def test_unavailable_mailbox_holds_collections_but_not_reminders(monkeypatch, tmp_path):
    import settlement_reminders.run as run_mod

    settings = _settings(tmp_path)
    history = SendHistory(settings.history_db)
    overdue = _overdue_agreement()
    # a preventive reminder due on 17/09: due 22/09 (Tuesday), 3 business days before
    upcoming = make_agreement(
        case_number=CASE_OTHER,
        client_emails="client2@x.com",
        installment_count=1,
        first_due=date(2026, 9, 22),
    )

    def _mailbox_down(settings_, summary, sender=None, today=None):
        raise RuntimeError("Graph unavailable")

    monkeypatch.setattr(run_mod, "_ingest_mailbox", _mailbox_down)
    s = _FakeSender()
    r = _run(monkeypatch, settings, [overdue, upcoming], s, history, date(2026, 9, 17))

    assert r.mailbox_unavailable
    assert r.collections_simulated == 0  # collection on hold
    assert r.simulated == 1  # the preventive reminder went on
    assert any("mailbox" in item for item in r.collections_held)
    assert [e["to"] for e in s.sent] == [["client2@x.com"]]


def test_a_reading_failure_today_blocks_only_this_run(monkeypatch, tmp_path):
    import settlement_reminders.run as run_mod

    settings = _settings(tmp_path)
    history = SendHistory(settings.history_db)
    agreement = _overdue_agreement()

    def _failed(settings_, summary, sender=None, today=None):
        summary.failed_cases.add(agreement.case_number)

    monkeypatch.setattr(run_mod, "_ingest_mailbox", _failed)
    r = _run(monkeypatch, settings, [agreement], _FakeSender(), history, date(2026, 9, 17))
    assert r.collections_simulated == 0
    assert len(r.collections_held) == 1

    # the next run with a good reading: collects, replaying the attempt that was held
    monkeypatch.setattr(run_mod, "_ingest_mailbox", lambda s_, r_, sender=None, today=None: None)
    r2 = _run(monkeypatch, settings, [agreement], _FakeSender(), history, date(2026, 9, 18))
    assert r2.collections_simulated == 1
    assert r2.collections_held == []
