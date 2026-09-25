"""The demo path end to end: fixture mailbox, recorded readings, outbox sender."""

from __future__ import annotations

from datetime import date

from settlement_reminders.config import Settings
from settlement_reminders.demo import FIXTURES, FixtureMailbox, FixtureReceiptReader, OutboxSender
from settlement_reminders.factory import build_history, build_ingest, build_sender
from settlement_reminders.run import execute
from tests.conftest import freeze_clock

CASE_A = "0001234-74.2099.5.99.0001"
CASE_D = "0000165-98.2099.5.99.0003"
CASE_B = "0001979-51.2099.5.99.0002"
CASE_DRAFT = "0001111-27.2099.8.99.0001"


def _demo_settings(tmp_path, today: date | None = None) -> Settings:
    settings = Settings(
        _env_file=None, demo_mode=True, history_db=str(tmp_path / "state.sqlite")
    ).for_demo()
    settings.excel_path = str(FIXTURES / "agreements.xlsx")
    settings.client_directory_path = str(FIXTURES / "clients.xlsx")
    settings.dry_run = False
    if today:
        settings.reference_date = today
    return settings


def _run(tmp_path, monkeypatch, today: date):
    settings = _demo_settings(tmp_path, today)
    freeze_clock(monkeypatch, today)
    sender = build_sender(settings)
    summary = execute(
        settings, sender, build_history(settings), ingest=build_ingest(settings, sender)
    )
    return settings, sender, summary


def test_reference_day_registers_settles_reminds_and_collects(tmp_path, monkeypatch):
    settings, sender, summary = _run(tmp_path, monkeypatch, date(2026, 9, 17))

    # two notices + one legacy draft registered; the test notice, the invalid notice and the
    # group traffic did not
    assert summary.ingested == 3
    assert summary.total_agreements == 5  # 3 from the store + 2 active rows of the spreadsheet
    assert len(summary.replies_settled) == 1 and CASE_A in summary.replies_settled[0]
    assert len(summary.replies_to_check) == 2  # the reply without a receipt + the invalid notice
    assert summary.errors == []

    # reminders: A's first installment was settled by the receipt (skipped); D's goes out
    assert summary.selected == 2
    assert summary.already_settled == 1
    assert summary.sent == 1
    # collections: B's installment was due yesterday -> attempt 1
    assert summary.collections_sent == 1
    assert summary.collections_held == []

    subjects = [e["subject"] for e in sender.sent]
    assert sum(s.startswith(f"ACORDO PACTUADO - {CASE_A}") for s in subjects) == 1
    assert sum(s.startswith(f"ACORDO PACTUADO - {CASE_D}") for s in subjects) == 1
    assert sum("Agreement registered" in s for s in subjects) == 2
    assert any("Notice not processed" in s for s in subjects)
    assert any(
        s.startswith("LEMBRETE DE PAGAMENTO DA 1ª PARCELA") and CASE_D in s for s in subjects
    )
    assert any(s.startswith("PAGAMENTO EM ABERTO - 1ª PARCELA") and CASE_B in s for s in subjects)
    assert not any(CASE_A in s and s.startswith("LEMBRETE") for s in subjects)

    # the notice to the client was written without the machine block and with the frame
    notice_file = next(
        e["file"] for e in sender.sent if e["subject"].startswith(f"ACORDO PACTUADO - {CASE_A}")
    )
    html = (sender.outbox / notice_file).read_text(encoding="utf-8")
    assert "AUTOMATION-DATA" not in html
    assert "cid:brand-mark" in html
    # D's reminder replied in the thread opened by its notice
    reminder = next(
        e for e in sender.sent if e["subject"].startswith("LEMBRETE") and CASE_D in e["subject"]
    )
    assert reminder["thread"].startswith("demo-thread-")
    assert "coordinator@lawfirm.example" in reminder["cc"]  # the notice's CC_EXTRA

    # the draft went through the matcher against the client directory
    from settlement_reminders.ingest import AgreementStore

    store = AgreementStore(settings.history_db)
    draft = store.find(CASE_DRAFT)
    assert draft.status == "active"
    assert draft.client_emails == [
        "finance@construtora-exemplo.example",
        "legal@construtora-exemplo.example",
    ]


def test_second_run_on_the_same_day_is_idempotent(tmp_path, monkeypatch):
    _run(tmp_path, monkeypatch, date(2026, 9, 17))
    _, sender, summary = _run(tmp_path, monkeypatch, date(2026, 9, 17))

    assert summary.ingested == 0
    assert summary.replies_settled == []
    assert summary.already_sent == 1
    assert summary.sent == 0
    assert summary.collections_sent == 0
    assert sender.sent == []


def test_advancing_the_clock_walks_the_collection_cycle_to_the_escalation(tmp_path, monkeypatch):
    _run(tmp_path, monkeypatch, date(2026, 9, 17))  # collection 1 of B
    for day in (date(2026, 9, 21), date(2026, 9, 23), date(2026, 9, 25), date(2026, 9, 29)):
        _, sender, summary = _run(tmp_path, monkeypatch, day)
        b_collections = [e for e in sender.sent if CASE_B in e["subject"]]
        assert len(b_collections) == 1, day
        assert b_collections[0]["subject"].startswith("PAGAMENTO EM ABERTO")
    # from 23/09 on, D's first installment (due 22/09, no receipt) is collected too: at
    # most one collection per agreement per day
    assert summary.collections_sent == 2
    _, sender, summary = _run(tmp_path, monkeypatch, date(2026, 9, 30))
    assert not any(CASE_B in e["subject"] for e in sender.sent)  # B's schedule is complete
    assert summary.sent == 1  # the reminder of C's first installment (due 05/10)
    _, sender, summary = _run(tmp_path, monkeypatch, date(2026, 10, 1))
    assert len(summary.escalated) == 1 and CASE_B in summary.escalated[0]
    escalation = next(e for e in sender.sent if "ESCALATION" in e["subject"])
    assert escalation["to"] == ["lawyer@lawfirm.example"]
    assert CASE_B in escalation["subject"]


def test_fixture_adapters_mirror_the_real_interfaces():
    mailbox = FixtureMailbox()
    messages = mailbox.recent_messages()
    assert len(messages) == 8
    receipt_msg = next(m for m in messages if m.id == "n2-receipt-a")
    attachments = mailbox.receipt_attachments(receipt_msg)
    assert [a.name for a in attachments] == ["receipt-3000.pdf"]
    assert receipt_msg.ignored == ["signature.vcf"]
    reading = FixtureReceiptReader().read(attachments[0].content, name=attachments[0].name)
    assert reading.is_receipt and str(reading.amount) == "3000.00"


def test_outbox_sender_writes_one_file_per_email(tmp_path):
    sender = OutboxSender(tmp_path / "outbox")
    thread = sender.send_tracked(
        to=["a@x.example"], subject="ACORDO PACTUADO - X", html="<p>hi</p>"
    )
    sender.reply_in_thread(
        conversation_id=thread,
        to=["a@x.example"],
        subject="LEMBRETE",
        html="<p>again</p>",
        cc=["c@x.example"],
    )
    files = sorted(p.name for p in (tmp_path / "outbox").glob("*.html"))
    assert len(files) == 2 and files[0].startswith("001-")
    assert f"Thread: {thread}" in (tmp_path / "outbox" / files[1]).read_text(encoding="utf-8")
