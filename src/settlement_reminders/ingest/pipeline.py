"""Ingestion pipeline: the mailbox sweep and the registration of drafts and notices.

Activation policy (conservative): an agreement is only born ACTIVE, generating automatic
reminders, when the extraction has high confidence, no doubts, AND the recipient was matched
with high confidence and has an e-mail. Any issue leaves the agreement PENDING, listed in
the exceptions digest for the team to resolve (complete the e-mail, review the draft...).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from html import escape
from pathlib import Path

from settlement_reminders.ingest.notice import (
    DEFAULT_INTERNAL_DOMAIN,
    InvalidNoticeError,
    ParsedNotice,
    has_data_block,
    parse_notice,
)
from settlement_reminders.ingest.store import AgreementStore
from settlement_reminders.mailer.sender import NOTICE_SUBJECT_PREFIX, is_test_subject

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    """What happened with one processed draft/notice."""

    case_number: str
    status: str  # active | pending | closed (was already closed)
    match_status: str
    client_emails: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    origin: str = ""


@dataclass
class MailboxSummary:
    """The outcome of one sweep of the ingestion mailbox."""

    messages: int = 0
    already_processed: int = 0
    no_pdf: int = 0  # messages without a draft (replies, notices, spam...)
    refused_sender: int = 0  # PDFs from senders outside the allowed list
    drafts: int = 0
    notices: int = 0  # notices from the skill (AUTOMATION-DATA block)
    own_copies: int = 0  # copies of the bot's own sends (Cc to itself)
    active: int = 0
    pending: int = 0
    closed: list[str] = field(default_factory=list)  # replies that closed an agreement
    settled: list[str] = field(default_factory=list)  # installments settled by receipt
    to_check: list[str] = field(default_factory=list)  # need a human
    errors: list[str] = field(default_factory=list)
    # collection ON HOLD today: a client attachment that did not settle (persisted in the
    # store until a human releases it)
    held: list[str] = field(default_factory=list)
    # cases whose reply FAILED in this run (retried tomorrow): no collection or escalation
    # today, because the failure may be hiding a payment
    failed_cases: set[str] = field(default_factory=set)

    def __str__(self) -> str:
        return (
            f"messages={self.messages} | already_processed={self.already_processed} | no_pdf={self.no_pdf} | "
            f"refused={self.refused_sender} | drafts={self.drafts} | notices={self.notices} | "
            f"own_copies={self.own_copies} | active={self.active} | pending={self.pending} | "
            f"closed={len(self.closed)} | settled={len(self.settled)} | to_check={len(self.to_check)} | "
            f"held={len(self.held)} | errors={len(self.errors)}"
        )


def process_draft(
    pdf: bytes | str | Path, *, extractor, matcher, store: AgreementStore, origin: str = ""
) -> IngestResult:
    """Processes a draft end to end and persists the result."""
    if isinstance(pdf, str | Path) and not origin:
        origin = Path(pdf).name

    extracted = extractor.extract_pdf(pdf, file_name=origin)
    match = matcher.match(extracted.payer)

    issues: list[str] = []
    if extracted.confidence != "high":
        issues.append(f"extraction with {extracted.confidence} confidence")
    issues.extend(f"extraction: {d}" for d in extracted.doubts)
    if match.status != "ok":
        issues.append(f"recipient ({match.status}): {match.rationale}")

    status = "active" if not issues else "pending"
    final_status = store.upsert(
        extracted,
        client_emails=match.emails,
        match_status=match.status,
        match_info=match.rationale,
        status=status,
        issues=issues,
        origin=origin,
    )

    result = IngestResult(
        case_number=extracted.case_number,
        status=final_status,
        match_status=match.status,
        client_emails=match.emails,
        issues=issues,
        origin=origin,
    )
    logger.info(
        "Draft processed: case=%s status=%s recipient=%s issues=%d",
        result.case_number,
        result.status,
        result.match_status,
        len(result.issues),
    )
    return result


def process_notice(
    content: str,
    *,
    store: AgreementStore,
    origin: str = "",
    internal_domain: str = DEFAULT_INTERNAL_DOMAIN,
) -> IngestResult:
    """Processes a notice from the skill (AUTOMATION-DATA block) and persists it.

    A deterministic path with no API cost: the data come structured by the skill, including
    the client's e-mails; there is no model extraction and no matcher. The same
    conservative policy applies: any doubt leaves the agreement PENDING. `InvalidNoticeError`
    propagates to the caller (it becomes an item for human checking).
    """
    return register_notice(
        parse_notice(content, internal_domain=internal_domain), store=store, origin=origin
    )


def register_notice(
    parsed: ParsedNotice, *, store: AgreementStore, origin: str = ""
) -> IngestResult:
    """Persists a `ParsedNotice` with the conservative policy."""
    agreement = parsed.agreement

    issues = [f"notice: {d}" for d in agreement.doubts]
    status = "active" if not issues else "pending"
    final_status = store.upsert(
        agreement,
        client_emails=parsed.client_emails,
        match_status="notice",
        match_info=f"e-mails given in the notice ({parsed.operation.lower()})",
        status=status,
        issues=issues,
        origin=origin,
        lawyer_email=parsed.lawyer,
        cc_extra=parsed.cc_extra,
    )

    result = IngestResult(
        case_number=agreement.case_number,
        status=final_status,
        match_status="notice",
        client_emails=parsed.client_emails,
        issues=issues,
        origin=origin,
    )
    logger.info(
        "Notice processed: case=%s operation=%s status=%s issues=%d",
        result.case_number,
        parsed.operation,
        result.status,
        len(result.issues),
    )
    return result


def dedup_emails(*groups: list[str] | None, exclude: list[str] | None = None) -> list[str]:
    """Joins e-mail lists without duplicates (case-insensitive) or empties."""
    seen = {e.lower() for e in (exclude or [])}
    out: list[str] = []
    for group in groups:
        for e in group or []:
            if e and e.lower() not in seen:
                seen.add(e.lower())
                out.append(e)
    return out


def notice_followups(
    parsed: ParsedNotice,
    result: IngestResult,
    content: str,
    *,
    sender,
    store: AgreementStore,
    bot_mailbox: str = "",
    confirmation_emails: list[str] | None = None,
    cc_notices: list[str] | None = None,
    days_before: int = 3,
    today: date | None = None,
    firm_name: str = "",
) -> None:
    """Forwards the notice to the CLIENT and confirms the registration to the team.

    - Forwarding: only an ACTIVE agreement; once per agreement (the `notice_sent_at` flag,
      written only on a REAL send: a DRY_RUN simulation does not consume the forward),
      except a CORRECTION, which forwards again. The body goes without the AUTOMATION-DATA
      block.
    - Confirmation: always (active or pending), to the notice's lawyer + `confirmation_emails`
      (e.g. the controllership: awareness, not approval).

    Failures propagate to the caller: the message is NOT marked as processed and the next
    sweep tries again (the flag prevents a duplicate forward to the client when only the
    confirmation failed).
    """
    from settlement_reminders.ingest.convert import installments_from_extracted
    from settlement_reminders.ingest.notice import strip_data_block
    from settlement_reminders.mailer import wrap_branded
    from settlement_reminders.rules import due_date, reminder_day

    today = today or date.today()
    agreement = parsed.agreement
    correction = parsed.operation == "CORRECTION"
    active = result.status == "active"

    # ---- 1. the notice to the client ----------------------------------------------------
    forwarded = False
    if (
        active
        and parsed.client_emails
        and (correction or not store.notice_sent(result.case_number))
    ):
        suffix = " (CORREÇÃO)" if correction else ""
        parties = f"{agreement.claimant.upper()} X {agreement.payer.upper()}"
        client_subject = f"{NOTICE_SUBJECT_PREFIX}{suffix} - {agreement.case_number} - {parties}"
        # Cc: lawyer + the agreement's copies + controllership + CC_NOTICES + the bot
        cc = dedup_emails(
            [parsed.lawyer],
            parsed.cc_extra,
            confirmation_emails,
            cc_notices,
            [bot_mailbox],
            exclude=parsed.client_emails,
        )
        # the institutional frame; the skill's body already carries its own signature
        client_html, images = wrap_branded(
            strip_data_block(content), signature=None, firm_name=firm_name
        )
        # send_tracked returns the conversation: reminders and collections will reply in it
        # (one thread per agreement). A sender without the method uses send.
        envelope = dict(
            to=list(parsed.client_emails),
            cc=cc,
            subject=client_subject,
            html=client_html,
            inline_images=images,
        )
        tracked = getattr(sender, "send_tracked", None)
        conversation = tracked(**envelope) if tracked is not None else None
        if tracked is None:
            sender.send(**envelope)
        if not getattr(sender, "dry_run", False):
            store.mark_notice_sent(result.case_number)
            if conversation:
                store.set_thread(result.case_number, conversation)
        forwarded = True

    # ---- 2. the registration confirmation to the team ----------------------------------
    recipients = dedup_emails([parsed.lawyer], confirmation_emails)
    if not recipients:
        logger.warning(
            "Notice %s without a confirmation recipient (lawyer/confirmation_emails)",
            result.case_number,
        )
        return

    installments = installments_from_extracted(agreement, parsed.client_emails)
    upcoming = [i for i in installments if due_date(i) >= today]

    lines = [
        f"<p><b>Case:</b> {escape(agreement.case_number)}<br>"
        f"<b>Client (payer):</b> {escape(agreement.payer)}<br>"
        f"<b>Installments:</b> {len(installments)}"
        + (
            f" (due from {min(i.original_due for i in installments):%d/%m/%Y}"
            f" to {max(i.original_due for i in installments):%d/%m/%Y})"
            if installments
            else ""
        )
        + "<br>"
        f"<b>Client e-mails:</b> {escape(', '.join(parsed.client_emails) or '—')}</p>"
    ]
    if active:
        if forwarded:
            lines.append(
                "<p>The agreement's notice was forwarded to the client from this mailbox.</p>"
            )
        if upcoming:
            target = min(upcoming, key=lambda i: reminder_day(i, days_before))
            lines.append(
                f"<p><b>Next reminder to the client:</b> {max(reminder_day(target, days_before), today):%d/%m/%Y} "
                f"(installment {target.number}, due {due_date(target):%d/%m/%Y}). After the due date without a receipt,"
                " the collection follows the automatic cycle until the installment is settled.</p>"
            )
        lines.append(
            "<p>No action is needed. To correct data, resend the notice through the skill in CORRECTION mode;"
            " to close the agreement, tell the automation's operator.</p>"
        )
        subject = f"[REMINDERS] Agreement registered - {agreement.case_number}"
    else:
        items = "".join(f"<li>{escape(i)}</li>" for i in result.issues)
        lines.append(
            "<p><b>The agreement did NOT enter the automatic cycle.</b> Issues:</p>"
            f"<ul>{items}</ul>"
            "<p>Correct the data and resend it through the skill (CORRECTION mode) to activate it.</p>"
        )
        subject = f"[REMINDERS] Agreement PENDING - {agreement.case_number}"

    sender.send(to=recipients, subject=subject, html="".join(lines))


def _warn_invalid_notice(sender, message, error: str) -> None:
    """Tells the sender why a notice was rejected (best effort)."""
    if sender is None or not message.sender:
        return
    try:
        sender.send(
            to=[message.sender],
            subject=f"[REMINDERS] Notice not processed - {message.subject}",
            html=(
                "<p>The notice below could not be registered by the automation:</p>"
                f"<pre style='background:#f4f5f7;padding:12px;border-radius:6px'>{escape(error)}</pre>"
                "<p>Generate the notice again through the <b>settlement-notice</b> skill and resend it."
                " In doubt, reply to this e-mail.</p>"
            ),
        )
    except Exception:  # noqa: BLE001 - a warning must never bring the sweep down
        logger.exception("Failed to warn %s about an invalid notice", message.sender)


# Reply/forward prefixes: the skill NEVER writes a notice with such a subject. A reply in the
# notice's thread QUOTES the AUTOMATION-DATA block of the original; without this filter the
# attached receipt would be mistaken for a re-registration and never read.
_REPLY_PREFIXES = ("re:", "res:", "enc:", "fw:", "fwd:")


def is_reply_subject(subject: str) -> bool:
    return (subject or "").strip().lower().startswith(_REPLY_PREFIXES)


def sender_may_submit(sender: str, suffixes: list[str]) -> bool:
    """Sender filter for DRAFTS and NOTICES. An empty list accepts anyone."""
    if not suffixes:
        return True
    s = (sender or "").lower()
    return bool(s) and any(s.endswith(x.lower()) for x in suffixes)


def process_mailbox(
    reader,
    *,
    extractor,
    matcher,
    store: AgreementStore,
    days: int = 7,
    allowed_senders: list[str] | None = None,
    sender=None,
    bot_mailbox: str = "",
    confirmation_emails: list[str] | None = None,
    cc_notices: list[str] | None = None,
    days_before: int = 3,
    today: date | None = None,
    receipt_reader=None,
    internal_domain: str = DEFAULT_INTERNAL_DOMAIN,
    firm_name: str = "",
) -> MailboxSummary:
    """Sweeps the mailbox and handles each message as a notice, a draft OR a client reply.

    Routing: when the subject references a case ALREADY in the database, the message is
    handled as a reply (even with a PDF attached: probably a receipt; a resent draft is not
    reprocessed by itself: use the CLI). Otherwise PDF attachments are ingested as drafts of
    new agreements.

    Idempotent: messages already recorded in the store are skipped. A message is only
    marked as processed when ALL its attachments were handled without error; transient
    failures (the API is down) are retried on the next run without duplicating agreements
    (the upsert is by case).

    Notices from the skill (an AUTOMATION-DATA block in the body) take priority over the
    other paths: they are checked BEFORE the routing by known case, so a CORRECTION of an
    already registered agreement is not mistaken for a client reply. Only internal messages
    addressed to the bot are candidates (the same filter as the drafts).
    """
    from settlement_reminders.ingest.replies import extract_case_number, handle_reply

    read_body = getattr(reader, "body", None)
    own_mailbox = (getattr(reader, "mailbox", "") or bot_mailbox or "").lower()

    summary = MailboxSummary()
    for message in reader.recent_messages(days=days, only_with_attachments=False):
        summary.messages += 1
        if store.message_processed(message.id):
            summary.already_processed += 1
            continue

        # a copy of the bot's own send (reminders/notices with the mailbox in Cc come back
        # to the inbox): ignore, or it would become a false "check" item on every real send
        if own_mailbox and (message.sender or "").lower() == own_mailbox:
            store.record_message(
                message.id,
                subject=message.subject,
                sender=message.sender,
                received_at=message.received_at,
                result="own send copy ignored",
            )
            summary.own_copies += 1
            continue

        may_submit = message.addressed_to_bot and sender_may_submit(
            message.sender, allowed_senders or []
        )

        # -- a notice from the skill? (internal, addressed to the bot, with a block, and an
        # -- ORIGINAL subject: a reply/forward quotes the block of the original notice and
        # -- is NOT a candidate) --
        if read_body is not None and may_submit and not is_reply_subject(message.subject):
            try:
                content = read_body(message)
            except Exception as exc:  # noqa: BLE001 - transient: try again next time
                summary.errors.append(
                    f"mailbox: failed to read the body of {message.subject!r}: {exc}"
                )
                logger.exception("Failed to fetch the body of message %s", message.id)
                continue
            if content and has_data_block(content):
                # rehearsals of the skill go out with [TEST] in the subject and must not
                # register an agreement, even when someone forwards them to the mailbox
                if is_test_subject(message.subject):
                    logger.info("Test notice ignored: %r", message.subject)
                    store.record_message(
                        message.id,
                        subject=message.subject,
                        sender=message.sender,
                        received_at=message.received_at,
                        result="test notice ignored",
                    )
                    continue
                origin = f"notice {message.subject!r} <{message.sender}>"
                try:
                    parsed = parse_notice(content, internal_domain=internal_domain)
                    r = register_notice(parsed, store=store, origin=origin)
                except InvalidNoticeError as exc:
                    # a deterministic defect: reprocessing does not fix it; a human does
                    detail = f"invalid notice in {message.subject!r} <{message.sender}>: {exc}"
                    summary.to_check.append(detail)
                    logger.warning("%s", detail)
                    _warn_invalid_notice(sender, message, str(exc))
                    store.record_message(
                        message.id,
                        subject=message.subject,
                        sender=message.sender,
                        received_at=message.received_at,
                        result=f"invalid notice: {exc}",
                    )
                    continue
                except Exception as exc:  # noqa: BLE001 - transient
                    summary.errors.append(f"notice {message.subject!r}: {exc}")
                    logger.exception("Failed to process notice %s", origin)
                    continue

                if sender is not None:
                    try:
                        notice_followups(
                            parsed,
                            r,
                            content,
                            sender=sender,
                            store=store,
                            bot_mailbox=bot_mailbox,
                            confirmation_emails=confirmation_emails,
                            cc_notices=cc_notices,
                            days_before=days_before,
                            today=today,
                            firm_name=firm_name,
                        )
                    except Exception as exc:  # noqa: BLE001 - transient: retry
                        summary.errors.append(f"follow-ups of notice {r.case_number}: {exc}")
                        logger.exception(
                            "Failed in the follow-ups of notice %s; the message will be reprocessed on the next sweep",
                            r.case_number,
                        )
                        continue
                summary.notices += 1
                summary.active += r.status == "active"
                summary.pending += r.status == "pending"
                store.record_message(
                    message.id,
                    subject=message.subject,
                    sender=message.sender,
                    received_at=message.received_at,
                    result=f"notice: {r.case_number} -> {r.status}",
                )
                continue

        known_case = (c := extract_case_number(message.subject)) and store.find(c) is not None

        # filters that apply only to DRAFTS (new agreements); replies about known cases
        # follow the normal flow, whoever sent them:
        # 1. the message must be addressed to the bot (To/Cc): group traffic the account
        #    receives is not a draft;
        # 2. the sender must be in the allowed list (when set).
        refusal = ""
        if not message.addressed_to_bot:
            refusal = "not addressed to the bot (group traffic)"
        elif not sender_may_submit(message.sender, allowed_senders or []):
            refusal = "sender outside the allowed list"

        # (a refused message without attachments follows the reply/ignored route)
        if not known_case and refusal and message.has_attachments:
            store.record_message(
                message.id,
                subject=message.subject,
                sender=message.sender,
                received_at=message.received_at,
                result=f"draft refused: {refusal}",
            )
            summary.refused_sender += 1
            logger.info("PDF ignored (%s): %r from %r", refusal, message.subject, message.sender)
            continue

        def _account_reply(reply, _message=message) -> None:
            if reply.action == "closed":
                summary.closed.append(reply.detail)
            elif reply.action in ("check", "unknown_agreement"):
                summary.to_check.append(reply.detail)
            if reply.settled:
                summary.settled.append(reply.detail)
            if reply.hold and reply.action != "closed":
                store.hold_collection(reply.case_number, reply.hold)
                summary.held.append(f"{reply.case_number}: {reply.hold}")
            store.record_message(
                _message.id,
                subject=_message.subject,
                sender=_message.sender,
                received_at=_message.received_at,
                result=f"reply: {reply.action}" + (f" ({reply.detail})" if reply.detail else ""),
            )
            summary.no_pdf += 1

        if known_case:
            # a reply about a registered agreement: the attachments are potential RECEIPTS
            # (PDF or screenshot), settled per installment through the model's reading
            reply_attachments: list = []
            if message.has_attachments and receipt_reader is not None:
                fetch = getattr(reader, "receipt_attachments", None) or reader.pdf_attachments
                reply_attachments = fetch(message)
            try:
                reply = handle_reply(
                    message,
                    store,
                    attachments=reply_attachments,
                    reader=receipt_reader,
                    internal_domain=internal_domain,
                )
            except Exception as exc:  # noqa: BLE001 - the reader is down: retry
                summary.errors.append(f"reply {message.subject!r}: {exc}")
                # the unread message may be the receipt: no collection of this case today
                summary.failed_cases.add(extract_case_number(message.subject))
                logger.exception("Failed to handle reply %s", message.id)
                continue
            _account_reply(reply)
            continue

        attachments = reader.pdf_attachments(message)
        if not attachments:
            _account_reply(handle_reply(message, store, internal_domain=internal_domain))
            continue

        parts: list[str] = []
        failed = False
        for attachment in attachments:
            origin = f"{attachment.name} <{message.sender}>"
            try:
                r = process_draft(
                    attachment.content,
                    extractor=extractor,
                    matcher=matcher,
                    store=store,
                    origin=origin,
                )
            except Exception as exc:  # noqa: BLE001 - one draft must not bring the sweep down
                failed = True
                summary.errors.append(f"ingestion {message.subject!r} / {attachment.name}: {exc}")
                logger.exception("Failed to process draft %s", origin)
                continue
            summary.drafts += 1
            summary.active += r.status == "active"
            summary.pending += r.status == "pending"
            parts.append(f"{attachment.name}: {r.case_number} -> {r.status}")

        if not failed:
            store.record_message(
                message.id,
                subject=message.subject,
                sender=message.sender,
                received_at=message.received_at,
                result="; ".join(parts),
            )

    logger.info("Mailbox sweep: %s", summary)
    return summary
