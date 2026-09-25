"""Handling of client replies in the mailbox: settlement PER INSTALLMENT.

The cycle of an installment only ends with a VALIDATED RECEIPT: the reply is matched to the
agreement by the case number in the subject, the attachments (PDF/screenshot) are read by
the model, and the match by amount settles the corresponding installment. When every
installment of the agreement is settled, the agreement is closed automatically.

Conservative policy:
- a reply WITHOUT an attached receipt neither closes nor settles anything: it becomes an
  item for human checking (a deliberate change: earlier, any client reply closed the whole
  agreement);
- an unknown sender (neither the client's e-mail nor internal) -> check;
- a receipt that matches no open installment -> check;
- a lawyer may forward the client's receipt (an internal sender is accepted): the document
  is what gets validated, not the sender.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from settlement_reminders.ingest.mailbox import Attachment, MailboxMessage
from settlement_reminders.ingest.notice import DEFAULT_INTERNAL_DOMAIN
from settlement_reminders.ingest.store import AgreementStore

logger = logging.getLogger(__name__)

_CASE_NUMBER = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")


def _deterministic_errors() -> tuple[type[Exception], ...]:
    """Reading failures that retrying does NOT fix (a defect of the file).

    Everything else (rate limit, unavailability, timeout) propagates to the caller on
    purpose: the message is not marked as processed and the next run tries again.
    """
    from anthropic import BadRequestError, UnprocessableEntityError

    from settlement_reminders.ingest.receipt import ReceiptError

    return (ReceiptError, BadRequestError, UnprocessableEntityError)


_DETERMINISTIC = _deterministic_errors()


@dataclass
class HandledReply:
    """The outcome of handling a reply."""

    action: str  # settled | closed | check | unknown_agreement | already_closed | ignored
    case_number: str = ""
    detail: str = ""
    settled: list[str] = field(default_factory=list)  # external_id of the installments
    # Reason to HOLD collection and escalation of the case until a human reconciles ("" =
    # nothing to hold). Filled when the reply brought an attachment that did not settle an
    # installment and was not discarded as a "non-receipt": a genuine receipt without a
    # matching installment, an illegible file or a type we cannot read.
    hold: str = ""


# signature/protocol attachments that never carry a receipt: they do not hold
_IRRELEVANT_ATTACHMENTS = (".vcf", ".ics", ".p7s", ".asc")


def _hold_reason(illegible: list[str], genuine_unmatched: list[str], unsupported: list[str]) -> str:
    parts = []
    if genuine_unmatched:
        parts.append("receipt(s) without a matching installment: " + "; ".join(genuine_unmatched))
    if illegible:
        parts.append("illegible attachment(s): " + "; ".join(illegible))
    if unsupported:
        parts.append("attachment(s) of an unsupported type: " + "; ".join(unsupported))
    return " | ".join(parts)


def extract_case_number(text: str) -> str:
    """The first CNJ case number found in the text (or empty)."""
    m = _CASE_NUMBER.search(text or "")
    return m.group(0) if m else ""


def handle_reply(
    message: MailboxMessage,
    store: AgreementStore,
    *,
    attachments: list[Attachment] | None = None,
    reader=None,
    internal_domain: str = DEFAULT_INTERNAL_DOMAIN,
) -> HandledReply:
    """Interprets a message about a known case (or without a case).

    Receipt reading errors (the API is down) PROPAGATE to the caller: the message must not
    be marked as processed, so it is tried again.
    """
    case_number = extract_case_number(message.subject)
    if not case_number:
        return HandledReply(action="ignored", detail="no case number in the subject")

    record = store.find(case_number)
    if record is None:
        return HandledReply(
            action="unknown_agreement",
            case_number=case_number,
            detail=f"case {case_number} is not in the agreements database",
        )
    if record.status == "closed":
        return HandledReply(
            action="already_closed",
            case_number=case_number,
            detail="the agreement was already closed",
        )

    sender = (message.sender or "").lower()
    known = {e.lower() for e in record.client_emails}
    internal = sender.endswith(f"@{internal_domain}")
    if not sender or (sender not in known and not internal):
        detail = (
            f"reply about case {case_number} came from an unrecognised sender ({message.sender or 'unknown'});"
            " nothing was settled: check"
        )
        logger.warning("%s", detail)
        return HandledReply(action="check", case_number=case_number, detail=detail)

    # attachments the mailbox discarded by type (.heic, .zip, an attached e-mail...): they
    # may be the receipt; we do not know, and that is exactly why they hold the collection
    unsupported = [
        n
        for n in (getattr(message, "ignored", None) or [])
        if not n.lower().endswith(_IRRELEVANT_ATTACHMENTS)
    ]

    if not attachments:
        if unsupported:
            detail = (
                f"reply from {message.sender} about case {case_number} with attachment(s) we cannot read"
                f" ({'; '.join(unsupported)}): check; collection ON HOLD until checked"
            )
            logger.warning("%s", detail)
            return HandledReply(
                action="check",
                case_number=case_number,
                detail=detail,
                hold=_hold_reason([], [], unsupported),
            )
        detail = (
            f"reply from {message.sender} about case {case_number} ({message.subject!r}) without an attached receipt:"
            " check (no installment was settled)"
        )
        logger.info("%s", detail)
        return HandledReply(action="check", case_number=case_number, detail=detail)

    if reader is None:
        names = [a.name for a in attachments]
        return HandledReply(
            action="check",
            case_number=case_number,
            detail=(
                f"receipt received from {message.sender} for case {case_number}, but receipt reading is unavailable"
                " (no API key): check manually; collection ON HOLD"
            ),
            hold=_hold_reason([], [], names + unsupported),
        )

    from settlement_reminders.ingest.receipt import match_installment

    installments = store.installments_of(case_number)
    if not installments:
        return HandledReply(
            action="check",
            case_number=case_number,
            detail=f"receipt received for case {case_number}, but the agreement is {record.status}"
            " (no active installments): check",
        )

    settled_before = store.settled_installments(case_number)
    settled_now: list[str] = []
    unmatched: list[str] = []
    illegible: list[str] = []
    genuine_unmatched: list[str] = []
    for attachment in attachments:
        try:
            receipt = reader.read(attachment.content, name=attachment.name)
        except _DETERMINISTIC as exc:
            # a defect of the file (invalid type, corrupted image): retrying does not fix it.
            # Record it and CONTINUE with the other attachments: one bad 'image.png' once
            # aborted the whole message and two real receipts in the same e-mail went
            # unsettled.
            logger.warning("Attachment %r illegible: %s", attachment.name, exc)
            unmatched.append(f"{attachment.name}: could not be read ({exc})")
            illegible.append(attachment.name)
            continue
        installment = match_installment(receipt, installments, settled_before)
        if installment is None:
            if receipt.is_receipt and not receipt.scheduled:
                # a REAL payment that did not match (a summed amount, the wrong installment,
                # another obligation): a human reconciles; until then, no collection
                reason = f"amount R$ {receipt.amount} matches no open installment"
                genuine_unmatched.append(f"{attachment.name} (R$ {receipt.amount})")
            else:
                reason = "not a receipt of a completed payment"
            unmatched.append(f"{attachment.name}: {reason}")
            continue
        detail = (
            f"R$ {receipt.amount}"
            + (f" on {receipt.paid_on:%d/%m/%Y}" if receipt.paid_on else "")
            + f" ({attachment.name}, from {message.sender})"
        )
        store.settle_installment(installment.external_id, case_number, "receipt", detail)
        settled_before.add(installment.external_id)
        settled_now.append(installment.external_id)
        logger.info("Installment %s settled by receipt: %s", installment.external_id, detail)

    hold = _hold_reason(illegible, genuine_unmatched, unsupported)

    if not settled_now:
        detail = (
            f"attachment(s) from {message.sender} for case {case_number} settled nothing: {'; '.join(unmatched)}: check"
            + ("; collection ON HOLD until checked" if hold else "")
        )
        logger.warning("%s", detail)
        return HandledReply(action="check", case_number=case_number, detail=detail, hold=hold)

    still_open = {i.external_id for i in installments} - settled_before
    extra = f" (attachments without settlement: {'; '.join(unmatched)})" if unmatched else ""
    if not still_open:
        reason = "every installment settled by receipt"
        store.close(case_number, reason)
        detail = f"receipt from {message.sender} settled {', '.join(settled_now)}; {reason}: agreement closed{extra}"
        logger.info("Agreement %s closed: %s", case_number, reason)
        return HandledReply(
            action="closed", case_number=case_number, detail=detail, settled=settled_now
        )

    detail = f"receipt from {message.sender} settled {', '.join(settled_now)}; {len(still_open)} installment(s) still open{extra}"
    return HandledReply(
        action="settled", case_number=case_number, detail=detail, settled=settled_now, hold=hold
    )
