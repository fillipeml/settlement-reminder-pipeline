"""The ingestion mailbox reader (Microsoft Graph faked)."""

from __future__ import annotations

import base64

import pytest

from settlement_reminders.ingest.mailbox import MailboxMessage, MailboxReader

_PDF = b"%PDF-1.4 test draft"
_PDF_B64 = base64.b64encode(_PDF).decode("ascii")


class _FakeGraph:
    def __init__(self, messages=None, attachments=None, single=None):
        self.messages = messages or []
        self.attachments = attachments or {}
        self.single = single or {}
        self.get_all_calls: list[tuple] = []
        self.get_calls: list[str] = []

    def get_all(self, path, params=None):
        self.get_all_calls.append((path, params))
        if path.endswith("/attachments"):
            msg_id = path.split("/messages/")[1].split("/")[0]
            return self.attachments.get(msg_id, [])
        return self.messages

    def get(self, path, params=None):
        self.get_calls.append(path)
        return self.single[path.rsplit("/", 1)[1]]


def _graph_message(msg_id="m1"):
    return {
        "id": msg_id,
        "subject": "FW: Settlement - Case X",
        "from": {"emailAddress": {"address": "controllership@lawfirm.example"}},
        "receivedDateTime": "2026-07-06T12:00:00Z",
        "hasAttachments": True,
        "toRecipients": [{"emailAddress": {"address": "automation@lawfirm.example"}}],
    }


def test_recent_messages_filters_by_date_and_attachment():
    graph = _FakeGraph(messages=[_graph_message()])
    reader = MailboxReader(graph, "automation@lawfirm.example")

    messages = reader.recent_messages(days=7)

    assert len(messages) == 1
    m = messages[0]
    assert m.id == "m1"
    assert m.subject.startswith("FW: Settlement")
    assert m.sender == "controllership@lawfirm.example"
    assert m.has_attachments is True
    assert m.addressed_to_bot is True

    path, params = graph.get_all_calls[0]
    assert "/users/automation@lawfirm.example/mailFolders/inbox/messages" in path
    assert "hasAttachments eq true" in params["$filter"]
    assert "receivedDateTime ge " in params["$filter"]


def test_pdf_attachments_filters_types_and_decodes():
    attachments = {
        "m1": [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "id": "a1",
                "name": "SETTLEMENT.pdf",
                "contentType": "application/pdf",
                "contentBytes": _PDF_B64,
            },
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "id": "a2",
                "name": "logo.png",
                "contentType": "image/png",
                "contentBytes": "aaaa",
            },
            {
                "@odata.type": "#microsoft.graph.itemAttachment",
                "id": "a3",
                "name": "forwarded message",
            },
        ]
    }
    reader = MailboxReader(_FakeGraph(attachments=attachments), "automation@lawfirm.example")
    message = MailboxMessage(id="m1", subject="FW: Settlement")

    pdfs = reader.pdf_attachments(message)

    assert len(pdfs) == 1
    assert pdfs[0].name == "SETTLEMENT.pdf"
    assert pdfs[0].content == _PDF
    assert message.ignored == ["logo.png", "forwarded message"]


def test_receipt_attachments_accept_images():
    attachments = {
        "m1": [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "id": "a1",
                "name": "pix.png",
                "contentType": "image/png",
                "contentBytes": base64.b64encode(b"\x89PNG").decode(),
            },
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "id": "a2",
                "name": "IMG.heic",
                "contentType": "image/heic",
                "contentBytes": "aaaa",
            },
        ]
    }
    reader = MailboxReader(_FakeGraph(attachments=attachments), "automation@lawfirm.example")
    message = MailboxMessage(id="m1")
    receipts = reader.receipt_attachments(message)
    assert [a.name for a in receipts] == ["pix.png"]
    assert message.ignored == ["IMG.heic"]


def test_large_attachment_is_fetched_individually():
    attachments = {
        "m1": [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "id": "a1",
                "name": "draft.pdf",
                "contentType": "application/pdf",
            }
        ]
    }  # no contentBytes
    single = {
        "a1": {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "id": "a1",
            "name": "draft.pdf",
            "contentBytes": _PDF_B64,
        }
    }
    graph = _FakeGraph(attachments=attachments, single=single)
    reader = MailboxReader(graph, "automation@lawfirm.example")

    pdfs = reader.pdf_attachments(MailboxMessage(id="m1"))

    assert pdfs[0].content == _PDF
    assert graph.get_calls  # fetched the attachment individually


def test_unconfigured_mailbox_fails_early():
    with pytest.raises(ValueError, match="INGEST_MAILBOX"):
        MailboxReader(_FakeGraph(), "")
