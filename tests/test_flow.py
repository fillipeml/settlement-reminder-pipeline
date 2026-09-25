"""The daily flow end to end with a structured source: selection, copies, idempotency, threads."""

from datetime import date

from settlement_reminders.config import Settings
from settlement_reminders.history import SendHistory
from settlement_reminders.mailer import EmailSender
from tests.conftest import freeze_clock
from tests.factories import CASE_OUTSIDER, CASE_PAYER_RULE, make_agreement


class _FakeSender(EmailSender):
    def __init__(self):
        self.sent = []

    def send(self, *, to, subject, html, cc=None, inline_images=None):
        self.sent.append({"to": to, "cc": cc or [], "subject": subject})


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


def test_selection_and_recipients(monkeypatch, tmp_path):
    settings = Settings(
        dry_run=True,
        days_before_due=3,
        ms_sender="reminders@lawfirm.example",
        controllership_emails="controllership@lawfirm.example",
        cc_notices="labour@lawfirm.example",
        history_db=str(tmp_path / "h.sqlite"),
    )
    agreement = make_agreement(
        client_emails="a@x.com, b@y.com",
        lawyer_email="lawyer@lawfirm.example",
        installment_count=3,
        first_due=date(2026, 6, 15),
    )
    sender = _FakeSender()
    history = SendHistory(settings.history_db)
    # 2nd installment: due 15/07 (Wed); 3 BUSINESS days before = 10/07 (Fri)
    summary = _run(monkeypatch, settings, [agreement], sender, history, date(2026, 7, 10))

    assert summary.selected == 1
    assert summary.simulated == 1
    env = sender.sent[0]
    assert env["to"] == ["a@x.com", "b@y.com"]
    # copies: the controllership (always) + CC_NOTICES only on the preventive reminders
    assert env["cc"] == ["controllership@lawfirm.example", "labour@lawfirm.example"]

    # gentle ramp: the reminder already went out today for this agreement, so the
    # collection of the 1st installment (due 15/06) waits for the next day
    assert summary.collections_simulated == 0

    r2 = _run(monkeypatch, settings, [agreement], sender, history, date(2026, 7, 13))
    assert r2.collections_simulated == 1
    collection = sender.sent[-1]
    assert collection["subject"].startswith("PAGAMENTO EM ABERTO - 1ª PARCELA")
    # the collection does NOT carry cc_notices
    assert collection["cc"] == ["controllership@lawfirm.example"]


def test_agreement_without_email_sends_nothing(monkeypatch, tmp_path):
    settings = Settings(dry_run=True, history_db=str(tmp_path / "h.sqlite"))
    agreement = make_agreement(client_emails="", installment_count=3, first_due=date(2026, 6, 15))
    sender = _FakeSender()
    history = SendHistory(settings.history_db)
    summary = _run(monkeypatch, settings, [agreement], sender, history, date(2026, 7, 10))

    assert summary.no_recipient == 1
    assert sender.sent == []


def test_idempotency(monkeypatch, tmp_path):
    settings = Settings(dry_run=True, history_db=str(tmp_path / "h.sqlite"))
    agreement = make_agreement(installment_count=3, first_due=date(2026, 6, 15))
    history = SendHistory(settings.history_db)

    r1 = _run(monkeypatch, settings, [agreement], _FakeSender(), history, date(2026, 7, 10))
    assert r1.simulated == 1
    r2 = _run(monkeypatch, settings, [agreement], _FakeSender(), history, date(2026, 7, 10))
    assert r2.simulated == 0
    assert r2.already_sent == 1


def test_settled_installment_gets_no_reminder(monkeypatch, tmp_path):
    """A receipt that arrives before the reminder day: the reminder is skipped."""
    from settlement_reminders.ingest import AgreementStore

    settings = Settings(dry_run=True, history_db=str(tmp_path / "h.sqlite"))
    agreement = make_agreement(installment_count=3, first_due=date(2026, 6, 15))
    second = agreement.installments()[1]
    AgreementStore(settings.history_db).settle_installment(
        second.external_id, agreement.case_number, "manual", "paid early"
    )
    sender = _FakeSender()
    summary = _run(
        monkeypatch,
        settings,
        [agreement],
        sender,
        SendHistory(settings.history_db),
        date(2026, 7, 10),
    )

    assert summary.selected == 1
    assert summary.already_settled == 1
    assert summary.simulated == 0
    # no reminder went out; the overdue 1st installment is still collected as usual
    assert not any(e["subject"].startswith("LEMBRETE") for e in sender.sent)
    assert summary.collections_simulated == 1


def test_cc_by_payer_reaches_only_the_client_of_the_rule(monkeypatch, tmp_path):
    """CC_BY_PAYER enters the reminders and the collections of the payer of the rule, and of
    nobody else."""
    settings = Settings(
        dry_run=True,
        days_before_due=3,
        ms_sender="reminders@lawfirm.example",
        controllership_emails="controllership@lawfirm.example",
        cc_by_payer="grupo modelo construtora: watcher@grupo-modelo.example;"
        " grupo modelo empreendimentos: watcher@grupo-modelo.example",
        history_db=str(tmp_path / "h.sqlite"),
    )
    assert settings.cc_by_payer == {
        "grupo modelo construtora": ["watcher@grupo-modelo.example"],
        "grupo modelo empreendimentos": ["watcher@grupo-modelo.example"],
    }

    # the payer of the rule (upper case, another defendant and an accent in the middle: the
    # match normalises)
    reached = make_agreement(
        case_number=CASE_PAYER_RULE,
        payer="GRUPO MODELO CONSTRUTORA E INCORPORAÇÃO LTDA; CIA. MELHORAMENTOS",
        installment_count=3,
        first_due=date(2026, 6, 15),
    )
    sender = _FakeSender()
    history = SendHistory(settings.history_db)
    _run(monkeypatch, settings, [reached], sender, history, date(2026, 7, 10))
    reminder = sender.sent[0]
    assert reminder["cc"] == ["controllership@lawfirm.example", "watcher@grupo-modelo.example"]

    # the collection (1st installment overdue) carries it too
    r2 = _run(monkeypatch, settings, [reached], sender, history, date(2026, 7, 13))
    assert r2.collections_simulated == 1
    assert "watcher@grupo-modelo.example" in sender.sent[-1]["cc"]

    # ANOTHER client of the firm: the rule does NOT reach it
    outsider = make_agreement(
        case_number=CASE_OUTSIDER,
        payer="GRUPO THERMAS CLUBE, TURISMO E LAZER",
        installment_count=3,
        first_due=date(2026, 6, 15),
    )
    other = _FakeSender()
    _run(
        monkeypatch,
        settings,
        [outsider],
        other,
        SendHistory(str(tmp_path / "h2.sqlite")),
        date(2026, 7, 10),
    )
    assert other.sent[0]["cc"] == ["controllership@lawfirm.example"]


# ------------------ one thread per agreement -------------------------------------------


class _ThreadSender(_FakeSender):
    """A fake that knows how to reply in the conversation (like the real EmailSender)."""

    def __init__(self):
        super().__init__()
        self.replies = []
        self.located = []

    def reply_in_thread(self, *, conversation_id, to, subject, html, cc=None, inline_images=None):
        self.replies.append({"conversation_id": conversation_id, "to": to, "subject": subject})
        return True

    def find_thread(self, case_number):
        self.located.append(case_number)
        return "conv-located"


def test_reminder_replies_in_the_notice_conversation(monkeypatch, tmp_path):
    settings = Settings(
        dry_run=True,
        days_before_due=3,
        controllership_emails="controllership@lawfirm.example",
        history_db=str(tmp_path / "h.sqlite"),
    )
    agreement = make_agreement(
        client_emails="a@x.com",
        installment_count=3,
        first_due=date(2026, 6, 15),
        thread_id="conv-1",
    )
    sender = _ThreadSender()
    _run(
        monkeypatch,
        settings,
        [agreement],
        sender,
        SendHistory(settings.history_db),
        date(2026, 7, 10),
    )

    assert sender.sent == []  # nothing as a new message
    assert len(sender.replies) == 1
    assert sender.replies[0]["conversation_id"] == "conv-1"
    assert sender.replies[0]["subject"].startswith("LEMBRETE DE PAGAMENTO")


def test_agreement_without_thread_locates_it_once_and_stores_it(monkeypatch, tmp_path):
    from settlement_reminders.ingest import AgreementStore

    settings = Settings(
        dry_run=True,
        days_before_due=3,
        controllership_emails="controllership@lawfirm.example",
        history_db=str(tmp_path / "h.sqlite"),
    )
    agreement = make_agreement(
        client_emails="a@x.com", installment_count=3, first_due=date(2026, 6, 15)
    )
    sender = _ThreadSender()
    _run(
        monkeypatch,
        settings,
        [agreement],
        sender,
        SendHistory(settings.history_db),
        date(2026, 7, 10),
    )

    assert sender.located == [agreement.case_number]
    assert sender.replies[0]["conversation_id"] == "conv-located"
    # structured-source agreements are not in the store: set_thread is a no-op
    assert AgreementStore(settings.history_db).without_thread() == []


def test_switch_off_sends_a_new_message(monkeypatch, tmp_path):
    settings = Settings(
        dry_run=True,
        days_before_due=3,
        thread_replies=False,
        controllership_emails="controllership@lawfirm.example",
        history_db=str(tmp_path / "h.sqlite"),
    )
    agreement = make_agreement(
        client_emails="a@x.com",
        installment_count=3,
        first_due=date(2026, 6, 15),
        thread_id="conv-1",
    )
    sender = _ThreadSender()
    _run(
        monkeypatch,
        settings,
        [agreement],
        sender,
        SendHistory(settings.history_db),
        date(2026, 7, 10),
    )

    assert sender.replies == []
    assert sender.located == []
    assert len(sender.sent) == 1
