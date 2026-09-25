"""The exceptions digest for the team."""

from __future__ import annotations

from settlement_reminders.mailer import EmailSender
from settlement_reminders.notifier import alert_exceptions, alert_failure, render_exceptions_digest
from tests.factories import CASE_EXTRACTED, CASE_OTHER


class _FakeSender(EmailSender):
    def __init__(self):
        self.sent = []

    def send(self, *, to, subject, html, cc=None, inline_images=None):
        self.sent.append({"to": to, "subject": subject, "html": html})


def test_digest_has_sections_and_counts_in_the_subject():
    subject, html = render_exceptions_digest(
        pending=[f"{CASE_EXTRACTED} [draft.pdf]: recipient (no_email)"],
        to_check=[f"reply about {CASE_OTHER} came from an unrecognised sender"],
        closed=["every installment settled by receipt - agreement closed"],
        settled=[f"receipt settled {CASE_EXTRACTED}#O1P1"],
        escalated=[f"{CASE_EXTRACTED}#O1P2 (installment 2) -> lawyer@lawfirm.example"],
        errors=["ingestion: API unavailable"],
    )

    assert "1 pending agreement(s)" in subject
    assert "1 to check" in subject
    assert "1 agreement(s) closed" in subject
    assert "1 settlement(s) by receipt" in subject
    assert "1 escalation(s)" in subject
    assert "1 error(s)" in subject
    assert CASE_EXTRACTED in html
    assert "unrecognised sender" in html
    assert "every installment settled" in html
    assert "ESCALATED" in html
    assert "API unavailable" in html


def test_content_is_html_escaped():
    _, html = render_exceptions_digest(
        pending=["case <script>alert(1)</script>"], to_check=[], closed=[], errors=[]
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_alert_is_sent_to_the_team():
    sender = _FakeSender()
    alert_exceptions(
        sender,
        ["controllership@lawfirm.example"],
        pending=["p1"],
        to_check=[],
        closed=[],
        errors=[],
    )
    assert len(sender.sent) == 1
    assert sender.sent[0]["to"] == ["controllership@lawfirm.example"]
    assert "[REMINDERS] Exceptions digest" in sender.sent[0]["subject"]


def test_without_recipients_it_only_logs():
    sender = _FakeSender()
    alert_exceptions(sender, [], pending=["p1"], to_check=[], closed=[], errors=[])
    alert_failure(sender, [], "boom")
    assert sender.sent == []


def test_failure_alert_carries_the_error():
    sender = _FakeSender()
    alert_failure(sender, ["operator@lawfirm.example"], "Graph <down>")
    assert sender.sent[0]["subject"].startswith("[ALERT]")
    assert "Graph &lt;down&gt;" in sender.sent[0]["html"]


def test_held_collections_lead_the_digest():
    subject, html = render_exceptions_digest(
        pending=[],
        to_check=[],
        closed=[],
        errors=[],
        held=[f"{CASE_OTHER}: receipt(s) without a matching installment"],
    )
    assert subject.startswith("[REMINDERS] Exceptions digest - 1 collection(s) ON HOLD")
    assert "Collections ON HOLD" in html
    assert CASE_OTHER in html
    assert "release-hold" in html
