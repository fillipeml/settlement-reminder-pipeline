"""Reading the ingestion mailbox through Microsoft Graph.

The team sends/forwards notices and drafts to the automation's mailbox. This module lists
recent messages and downloads attachments. It needs only `Mail.Read` (Application): the
mailbox is never modified; the "already processed" control lives in SQLite (see
`store.record_message`), which keeps the permission minimal and the audit on our side.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

_PDF_MIME = "application/pdf"
_FILE_ATTACHMENT = "#microsoft.graph.fileAttachment"
RECEIPT_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp")


@dataclass
class Attachment:
    """A downloaded attachment."""

    name: str
    content: bytes


@dataclass
class MailboxMessage:
    """Metadata of a message of the ingestion mailbox."""

    id: str
    subject: str = ""
    sender: str = ""
    received_at: str = ""
    has_attachments: bool = False
    # True when the bot's mailbox is in To/Cc. Messages of groups the account belongs to
    # arrive in the inbox WITHOUT addressing the bot and are not notice/draft candidates.
    addressed_to_bot: bool = True
    ignored: list[str] = field(default_factory=list)  # attachments not downloaded (type)


class MailboxReader:
    """Reads messages and attachments of a mailbox through Microsoft Graph."""

    def __init__(self, graph, mailbox: str) -> None:
        if not mailbox:
            raise ValueError("Ingestion mailbox (INGEST_MAILBOX) not configured.")
        self.graph = graph
        self.mailbox = mailbox

    def recent_messages(
        self, *, days: int = 7, only_with_attachments: bool = True
    ) -> list[MailboxMessage]:
        """Messages of the Inbox received in the last `days`."""
        since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        filter_ = f"receivedDateTime ge {since}"
        if only_with_attachments:
            filter_ += " and hasAttachments eq true"

        items = self.graph.get_all(
            f"/users/{self.mailbox}/mailFolders/inbox/messages",
            params={
                "$filter": filter_,
                "$orderby": "receivedDateTime desc",
                "$select": "id,subject,from,receivedDateTime,hasAttachments,toRecipients,ccRecipients",
                "$top": "50",
            },
        )
        mailbox = self.mailbox.lower()
        messages = []
        for item in items:
            recipients = {
                (r.get("emailAddress") or {}).get("address", "").lower()
                for r in (item.get("toRecipients") or []) + (item.get("ccRecipients") or [])
            }
            messages.append(
                MailboxMessage(
                    id=item["id"],
                    subject=item.get("subject") or "",
                    sender=((item.get("from") or {}).get("emailAddress", {}).get("address", "")),
                    received_at=item.get("receivedDateTime") or "",
                    has_attachments=bool(item.get("hasAttachments")),
                    addressed_to_bot=mailbox in recipients,
                )
            )
        logger.info("Mailbox %s: %d message(s) since %s", self.mailbox, len(messages), since)
        return messages

    def body(self, message: MailboxMessage) -> str:
        """The message body (HTML or text), fetched on demand.

        The listing carries no bodies (a smaller payload); only notice candidates (an
        internal message addressed to the bot) need it, to look for the AUTOMATION-DATA
        block.
        """
        item = self.graph.get(
            f"/users/{self.mailbox}/messages/{message.id}", params={"$select": "body"}
        )
        return ((item.get("body") or {}).get("content")) or ""

    def pdf_attachments(self, message: MailboxMessage) -> list[Attachment]:
        """Downloads the PDF attachments (drafts); the rest goes to `ignored`."""
        return self._attachments(message, extensions=(".pdf",))

    def receipt_attachments(self, message: MailboxMessage) -> list[Attachment]:
        """Attachments that may be receipts: PDF and images (PIX screenshots)."""
        return self._attachments(message, extensions=RECEIPT_EXTENSIONS)

    def _attachments(
        self, message: MailboxMessage, *, extensions: tuple[str, ...]
    ) -> list[Attachment]:
        items = self.graph.get_all(f"/users/{self.mailbox}/messages/{message.id}/attachments")
        attachments: list[Attachment] = []
        for item in items:
            name = item.get("name") or "(unnamed)"
            if item.get("@odata.type") != _FILE_ATTACHMENT:
                message.ignored.append(name)
                continue
            type_ok = (
                (item.get("contentType") or "").lower() == _PDF_MIME and ".pdf" in extensions
            ) or name.lower().endswith(extensions)
            if not type_ok:
                message.ignored.append(name)
                logger.warning(
                    "Attachment ignored (type outside %s): %r in %r",
                    extensions,
                    name,
                    message.subject,
                )
                continue

            content_b64 = item.get("contentBytes")
            if content_b64 is None:
                # large attachments may come without the content in the listing
                item = self.graph.get(
                    f"/users/{self.mailbox}/messages/{message.id}/attachments/{item['id']}"
                )
                content_b64 = item.get("contentBytes", "")
            attachments.append(Attachment(name=name, content=base64.b64decode(content_b64)))
        return attachments
