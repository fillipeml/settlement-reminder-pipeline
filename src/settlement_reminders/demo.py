"""Demo adapters: the whole flow offline, on fictional data.

- `FixtureMailbox` replays messages described in `fixtures/mailbox/messages.json`;
- `FixtureReceiptReader` and `FixtureExtractor` return the readings recorded in
  `fixtures/receipts.json` and `fixtures/drafts.json` (keyed by attachment name), so no
  model is called;
- `OutboxSender` writes every e-mail as a file in the outbox folder, exactly what a
  recipient would get, and reports threads like the real sender.

Every party, e-mail and case number in the fixtures is invented (the domain
`lawfirm.example` is reserved; the case numbers are dated 2099 with invalid check digits).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from settlement_reminders.ingest.extract import check_consistency
from settlement_reminders.ingest.mailbox import RECEIPT_EXTENSIONS, Attachment, MailboxMessage
from settlement_reminders.ingest.receipt import ExtractedReceipt, ReceiptError
from settlement_reminders.ingest.schema import ExtractedAgreement

logger = logging.getLogger(__name__)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


class FixtureMailbox:
    """Replays the messages of a JSON fixture (the same interface as `MailboxReader`)."""

    def __init__(
        self,
        path: Path | str = FIXTURES / "mailbox" / "messages.json",
        mailbox: str = "automation@lawfirm.example",
    ) -> None:
        self.path = Path(path)
        self.mailbox = mailbox
        self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def recent_messages(
        self, *, days: int = 7, only_with_attachments: bool = False
    ) -> list[MailboxMessage]:
        messages = []
        for item in self._data["messages"]:
            if only_with_attachments and not item.get("attachments"):
                continue
            messages.append(
                MailboxMessage(
                    id=item["id"],
                    subject=item.get("subject", ""),
                    sender=item.get("sender", ""),
                    received_at=item.get("received_at", ""),
                    has_attachments=bool(item.get("attachments")),
                    addressed_to_bot=item.get("addressed_to_bot", True),
                )
            )
        return messages

    def _item(self, message: MailboxMessage) -> dict:
        return next(i for i in self._data["messages"] if i["id"] == message.id)

    def body(self, message: MailboxMessage) -> str:
        item = self._item(message)
        if "body_file" in item:
            return (self.path.parent / item["body_file"]).read_text(encoding="utf-8")
        return item.get("body", "")

    def pdf_attachments(self, message: MailboxMessage) -> list[Attachment]:
        return self._attachments(message, extensions=(".pdf",))

    def receipt_attachments(self, message: MailboxMessage) -> list[Attachment]:
        return self._attachments(message, extensions=RECEIPT_EXTENSIONS)

    def _attachments(
        self, message: MailboxMessage, *, extensions: tuple[str, ...]
    ) -> list[Attachment]:
        out: list[Attachment] = []
        for att in self._item(message).get("attachments", []):
            name = att["name"]
            if not name.lower().endswith(extensions):
                message.ignored.append(name)
                continue
            content = (self.path.parent / att["file"]).read_bytes() if att.get("file") else b""
            out.append(Attachment(name=name, content=content))
        return out


class FixtureReceiptReader:
    """Returns the recorded reading of each receipt, by attachment name."""

    def __init__(self, path: Path | str = FIXTURES / "receipts.json") -> None:
        self._readings = json.loads(Path(path).read_text(encoding="utf-8"))

    def read(self, content: bytes, *, name: str = "") -> ExtractedReceipt:
        if name not in self._readings:
            raise ReceiptError(f"unsupported attachment type: {name!r}")
        return ExtractedReceipt.model_validate(self._readings[name])


class FixtureExtractor:
    """Returns the recorded extraction of each draft, by file name."""

    def __init__(self, path: Path | str = FIXTURES / "drafts.json") -> None:
        self._drafts = json.loads(Path(path).read_text(encoding="utf-8"))

    def extract_pdf(self, pdf, *, file_name: str = "") -> ExtractedAgreement:
        key = re.sub(r"\s*<.*$", "", file_name)  # "draft.pdf <sender>" -> "draft.pdf"
        if key not in self._drafts:
            raise RuntimeError(f"no recorded extraction for {file_name!r}")
        agreement = ExtractedAgreement.model_validate(self._drafts[key])
        agreement.doubts.extend(check_consistency(agreement))
        if agreement.doubts and agreement.confidence == "high":
            agreement.confidence = "medium"
        return agreement


class OutboxSender:
    """Writes every e-mail to a folder instead of sending it; tracks threads like Graph."""

    dry_run = False

    def __init__(self, outbox: Path | str, sender: str = "automation@lawfirm.example") -> None:
        self.outbox = Path(outbox)
        self.outbox.mkdir(parents=True, exist_ok=True)
        self.sender = sender
        self.sent: list[dict] = []
        self._threads = 0

    def _write(self, *, to, cc, subject, html, thread: str = "") -> None:
        n = len(list(self.outbox.glob("*.html"))) + 1
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", subject)[:70].strip("-")
        path = self.outbox / f"{n:03d}-{safe}.html"
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
        header = (
            f"<!-- From: {self.sender}\n     To: {', '.join(to)}\n     Cc: {', '.join(cc or [])}\n"
            f"     Subject: {subject}\n     Thread: {thread or '(new message)'}\n     Written: {stamp} -->\n"
        )
        path.write_text(header + html, encoding="utf-8")
        self.sent.append(
            {
                "to": list(to),
                "cc": list(cc or []),
                "subject": subject,
                "thread": thread,
                "file": path.name,
            }
        )
        logger.info("[OUTBOX] %s -> to=%s cc=%s", subject, to, cc or [])

    def send(self, *, to, subject, html, cc=None, inline_images=None) -> None:
        self._write(to=to, cc=cc, subject=subject, html=html)

    def send_tracked(self, *, to, subject, html, cc=None, inline_images=None) -> str:
        self._threads += 1
        thread = f"demo-thread-{self._threads}"
        self._write(to=to, cc=cc, subject=subject, html=html, thread=thread)
        return thread

    def find_thread(self, case_number: str) -> str | None:
        return None

    def reply_in_thread(
        self, *, conversation_id, to, subject, html, cc=None, inline_images=None
    ) -> bool:
        self._write(to=to, cc=cc, subject=subject, html=html, thread=conversation_id)
        return True
