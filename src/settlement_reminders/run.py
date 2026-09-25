"""The daily routine.

Flow: mailbox sweep (notices/drafts/replies) -> sources (agreements) -> expand installments
-> preventive reminder (BUSINESS days before the due date) -> COLLECTION cycle of the
overdue ones without a settlement (every N business days, with a cap and an escalation) ->
history -> exceptions digest.

Copies: the client in To; the controllership in Cc of everything; the lawyer only gets the
registration confirmation and the escalation. Run with: settlement-reminders
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from html import escape

from settlement_reminders.business_days import is_business_day, use_subdivision
from settlement_reminders.config import Settings
from settlement_reminders.history import SendHistory
from settlement_reminders.mailer import render_collection, render_reminder, wrap_branded
from settlement_reminders.rules import (
    collections_due,
    due_date,
    schedule_exhausted,
    select_installments,
    today_in_tz,
)
from settlement_reminders.sources import build_sources

logger = logging.getLogger(__name__)


@dataclass
class Summary:
    total_agreements: int = 0
    total_installments: int = 0
    selected: int = 0
    sent: int = 0
    simulated: int = 0
    already_sent: int = 0
    already_settled: int = 0
    no_recipient: int = 0
    failures: int = 0
    collections_sent: int = 0
    collections_simulated: int = 0
    escalated: list[str] = field(default_factory=list)
    ingested: int = 0
    ingest_failures: int = 0
    replies_closed: list[str] = field(default_factory=list)
    replies_settled: list[str] = field(default_factory=list)
    replies_to_check: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # guards of the collection cycle (never collect from someone who may have paid): a
    # mailbox that could not be read, or a reply that failed to read, may be hiding a
    # receipt; in those cases nothing is collected or escalated
    mailbox_unavailable: bool = False
    failed_cases: set[str] = field(default_factory=set)
    collections_held: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"agreements={self.total_agreements} | installments={self.total_installments} | "
            f"selected={self.selected} | sent={self.sent} | simulated={self.simulated} | "
            f"already_sent={self.already_sent} | already_settled={self.already_settled} | "
            f"collections={self.collections_sent} | collections_simulated={self.collections_simulated} | "
            f"held={len(self.collections_held)} | escalated={len(self.escalated)} | "
            f"no_recipient={self.no_recipient} | failures={self.failures} | "
            f"ingested={self.ingested} | ingest_failures={self.ingest_failures}"
        )


Ingest = Callable[[Summary], None]


def _ingest_mailbox(
    settings: Settings, summary: Summary, sender=None, today: date | None = None
) -> None:
    """Sweeps the ingestion mailbox and processes new notices/drafts/replies (when configured).

    Without INGEST_MAILBOX the step is silently skipped. With the mailbox configured but the
    API key, the Graph credentials or the client directory missing, the step is skipped with
    a warning: the SENDING routine never stops running because of the ingestion.
    """
    if not settings.ingest_mailbox:
        return

    missing = [
        name
        for name, value in (
            ("ANTHROPIC_API_KEY", settings.anthropic_api_key),
            (
                "MS_TENANT_ID/MS_CLIENT_ID/MS_CLIENT_SECRET",
                settings.ms_tenant_id and settings.ms_client_id and settings.ms_client_secret,
            ),
            ("CLIENT_DIRECTORY_PATH", settings.client_directory_path),
        )
        if not value
    ]
    if missing:
        logger.warning("Ingestion skipped: configuration missing (%s).", ", ".join(missing))
        return

    from settlement_reminders.graph import GraphClient
    from settlement_reminders.ingest import (
        AgreementExtractor,
        AgreementStore,
        ExcelClientDirectory,
        RecipientMatcher,
    )
    from settlement_reminders.ingest.mailbox import MailboxReader
    from settlement_reminders.ingest.receipt import ReceiptReader

    contacts = ExcelClientDirectory(settings.client_directory_path).fetch_contacts()
    graph = GraphClient(settings.ms_tenant_id, settings.ms_client_id, settings.ms_client_secret)
    run_ingest(
        settings,
        summary,
        reader=MailboxReader(graph, settings.ingest_mailbox),
        extractor=AgreementExtractor(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        ),
        matcher=RecipientMatcher(
            contacts, api_key=settings.anthropic_api_key, model=settings.anthropic_model
        ),
        receipt_reader=ReceiptReader(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        ),
        store=AgreementStore(settings.history_db),
        sender=sender,
        today=today,
    )


def run_ingest(
    settings: Settings,
    summary: Summary,
    *,
    reader,
    extractor,
    matcher,
    receipt_reader,
    store,
    sender,
    today: date | None,
) -> None:
    """Runs the sweep with already-built collaborators and folds the outcome into the summary."""
    from settlement_reminders.ingest.pipeline import process_mailbox

    outcome = process_mailbox(
        reader,
        extractor=extractor,
        matcher=matcher,
        store=store,
        days=settings.ingest_lookback_days,
        allowed_senders=settings.ingest_allowed_senders,
        sender=sender,
        bot_mailbox=settings.ms_sender,
        confirmation_emails=settings.controllership_emails,
        cc_notices=settings.cc_notices,
        days_before=settings.days_before_due,
        today=today or today_in_tz(settings.timezone, settings.reference_date),
        receipt_reader=receipt_reader,
        internal_domain=settings.internal_domain,
        firm_name=settings.firm_name,
    )
    summary.ingested = outcome.drafts + outcome.notices
    summary.ingest_failures = len(outcome.errors)
    summary.replies_closed = outcome.closed
    summary.replies_settled = outcome.settled
    summary.replies_to_check = outcome.to_check
    summary.failed_cases = set(outcome.failed_cases)
    summary.errors.extend(outcome.errors)


def _collect_installments(settings: Settings, summary: Summary) -> list:
    """Reads the sources, dedups by case and expands into installments."""
    installments: list = []
    seen: set[str] = set()

    if "notices" in settings.sources:
        from settlement_reminders.ingest.store import AgreementStore

        from_store = AgreementStore(settings.history_db).installments_for_reminders()
        installments.extend(from_store)
        cases = {i.agreement.case_number for i in from_store}
        seen.update(cases)
        summary.total_agreements += len(cases)

    for source in build_sources(settings):
        for agreement in source.fetch_agreements():
            if agreement.case_number in seen:
                continue
            seen.add(agreement.case_number)
            summary.total_agreements += 1
            installments.extend(agreement.installments())
    summary.total_installments = len(installments)
    return installments


def _build_cc(settings: Settings, to: list[str], extras: list | None = None) -> list[str]:
    """Cc of the e-mails to the client: controllership + the extra copies received.

    Only the controllership follows by default. The extra copies come from the agreement
    (the notice's CC_EXTRA field), from CC_NOTICES (reminders only) and from CC_BY_PAYER
    (a rule per paying client); the caller composes them.
    """
    seen = {e.lower() for e in to}
    cc: list[str] = []
    for e in list(settings.controllership_emails) + [str(x) for x in (extras or [])]:
        if e and e.lower() not in seen:
            seen.add(e.lower())
            cc.append(e)
    return cc


def _send_to_client(sender, agreement, store, settings: Settings, **envelope) -> None:
    """A reminder/collection to the client: REPLIES in the notice's conversation when there
    is a thread (one thread per agreement); otherwise a new message. An agreement without a
    linked thread tries to locate it in the Sent Items once and stores it. Senders without
    the methods (fakes, legacy) use send().
    """
    thread = getattr(agreement, "thread_id", "") if settings.thread_replies else ""
    reply = getattr(sender, "reply_in_thread", None)
    locate = getattr(sender, "find_thread", None)
    if settings.thread_replies and not thread and locate is not None:
        thread = locate(agreement.case_number) or ""
        if thread and store is not None:
            store.set_thread(agreement.case_number, thread)
            agreement.thread_id = thread
    if thread and reply is not None:
        reply(conversation_id=thread, **envelope)
        return
    sender.send(**envelope)


def _collection_pass(
    installments: list,
    settings: Settings,
    sender,
    history: SendHistory,
    store,
    summary: Summary,
    today,
    already_contacted: set[str] | None = None,
) -> None:
    """Collects overdue installments without a settlement; when the schedule is exhausted, escalates.

    Gentle ramp: at most ONE collection per AGREEMENT per day, never on the same day as a
    preventive reminder of the agreement, and among the overdue installments the one with
    FEWER attempts is collected first (a rotation: each installment is presented once
    before any repetition; ties go to the oldest due date).

    Idempotency through the history: every attempt is recorded as `{external_id}#C{n}` (and
    the escalation as `{external_id}#ESCALATION`), so missed runs are caught up and nothing
    is sent twice; `collected_today` covers a second run of the routine on the same day. In
    DRY_RUN the simulation does not consume the real send (the same discipline as the
    reminders).
    """
    interval = settings.collection_interval_business_days
    cap = settings.collection_max_attempts
    if cap <= 0 or not is_business_day(today):
        return

    if summary.mailbox_unavailable:
        notice = (
            "ALL: the mailbox is unavailable in this run; without receipt reading,"
            " no collection or escalation goes out today"
        )
        summary.collections_held.append(notice)
        logger.warning("Collections ON HOLD: %s", notice)
        return

    # persisted holds (an unreconciled client attachment) + today's failures
    holds = {c: reason for c, reason, _ in store.holds()}
    held_today: set[str] = set()

    contacted = set(already_contacted or ())
    settled: dict[str, set[str]] = {}
    # candidates per case: (next_attempt, due_date, installment)
    candidates: dict[str, list[tuple[int, object, object]]] = {}
    escalatable: list = []

    for installment in installments:
        due = due_date(installment)
        if today <= due:
            continue
        case = installment.agreement.case_number
        hold_reason = holds.get(case) or (
            "a client reply failed to read in this run (automatic retry on the next one)"
            if case in summary.failed_cases
            else ""
        )
        if hold_reason:
            # applies to collection AND escalation: no pressure on someone who may have paid
            if case not in held_today:
                held_today.add(case)
                summary.collections_held.append(f"{case}: {hold_reason}")
                logger.warning("Collection/escalation ON HOLD for %s: %s", case, hold_reason)
            continue
        if case not in settled:
            settled[case] = store.settled_installments(case)
        if installment.external_id in settled[case]:
            continue
        if not installment.agreement.client_emails:
            logger.warning("Collection skipped (no e-mail): %s", installment.external_id)
            continue

        due_count = collections_due(installment, today, interval=interval, cap=cap)
        attempt = next(
            (
                n
                for n in range(1, due_count + 1)
                if not history.already_sent(
                    f"{installment.external_id}#C{n}", due, count_simulated=settings.dry_run
                )
            ),
            None,
        )
        if attempt is not None:
            candidates.setdefault(case, []).append((attempt, due, installment))
        elif due_count >= cap and schedule_exhausted(
            installment, today, interval=interval, cap=cap
        ):
            escalatable.append(installment)

    for case, queue in candidates.items():
        if case in contacted or history.collected_today(
            f"{case}#", today, count_simulated=settings.dry_run
        ):
            continue
        attempt, due, installment = min(queue, key=lambda c: (c[0], c[1]))
        to = [str(e) for e in installment.agreement.client_emails]
        cc = _build_cc(
            settings,
            to,
            list(installment.agreement.cc_emails)
            + settings.cc_for_payer(installment.agreement.payer),
        )
        subject, body = render_collection(installment)
        html, images = wrap_branded(
            body, signature=settings.signature_department, firm_name=settings.firm_name
        )
        try:
            _send_to_client(
                sender,
                installment.agreement,
                store,
                settings,
                to=to,
                cc=cc,
                subject=subject,
                html=html,
                inline_images=images,
            )
        except Exception as exc:  # noqa: BLE001
            summary.failures += 1
            summary.errors.append(f"collection {installment.external_id}#C{attempt}: {exc}")
            logger.exception("Failed collection %s", installment.external_id)
            history.record(
                run_day=today,
                external_id=f"{installment.external_id}#C{attempt}",
                due=due,
                case_number=case,
                client_email=", ".join(to),
                source="collection",
                status="failed",
                error=str(exc),
            )
            continue
        status = "simulated" if settings.dry_run else "sent"
        summary.collections_simulated += settings.dry_run
        summary.collections_sent += not settings.dry_run
        history.record(
            run_day=today,
            external_id=f"{installment.external_id}#C{attempt}",
            due=due,
            case_number=case,
            client_email=", ".join(to),
            source="collection",
            status=status,
        )
        contacted.add(case)

    # every attempt already went out and the window ended: a single escalation per
    # installment (to the lawyer, not the client; outside the daily ramp)
    for installment in escalatable:
        due = due_date(installment)
        case = installment.agreement.case_number
        to = [str(e) for e in installment.agreement.client_emails]
        if history.already_sent(
            f"{installment.external_id}#ESCALATION", due, count_simulated=settings.dry_run
        ):
            continue
        agreement = installment.agreement
        recipients = (
            [str(agreement.lawyer_email)] if agreement.lawyer_email else list(settings.alert_emails)
        )
        if not recipients:
            summary.errors.append(
                f"escalation without a recipient (lawyer/ALERT_EMAILS): {installment.external_id}"
            )
            continue
        subject = f"[REMINDERS] ESCALATION - installment {installment.number} unpaid - {agreement.case_number}"
        html = (
            f"<p>Installment <b>{installment.number}</b> of agreement <b>{escape(agreement.case_number)}</b>"
            f" ({escape(agreement.claimant)} X {escape(agreement.payer)}) was due on <b>{due:%d/%m/%Y}</b> and, after"
            f" <b>{cap} collections</b> sent to the client ({escape(', '.join(to))}), no receipt was received.</p>"
            "<p><b>The automation stopped collecting this installment.</b> The contact is now human. When the case is"
            " resolved, ask the automation's operator to settle the installment (or to close the agreement).</p>"
        )
        try:
            sender.send(
                to=recipients, cc=_build_cc(settings, recipients), subject=subject, html=html
            )
        except Exception as exc:  # noqa: BLE001
            summary.failures += 1
            summary.errors.append(f"escalation {installment.external_id}: {exc}")
            logger.exception("Failed escalation %s", installment.external_id)
            continue
        status = "simulated" if settings.dry_run else "sent"
        summary.escalated.append(
            f"{installment.external_id} (installment {installment.number}, due {due:%d/%m/%Y}) -> {', '.join(recipients)}"
        )
        history.record(
            run_day=today,
            external_id=f"{installment.external_id}#ESCALATION",
            due=due,
            case_number=case,
            client_email=", ".join(recipients),
            source="escalation",
            status=status,
        )


def execute(
    settings: Settings, sender, history: SendHistory, *, ingest: Ingest | None = None
) -> Summary:
    """One run of the daily routine. `ingest` replaces the real mailbox sweep (the demo)."""
    summary = Summary()
    use_subdivision(settings.holiday_subdivision)
    today = today_in_tz(settings.timezone, settings.reference_date)

    try:
        if ingest is not None:
            ingest(summary)
        else:
            _ingest_mailbox(settings, summary, sender, today)
    except Exception as exc:  # noqa: BLE001 - reminders go on; collections do not
        summary.ingest_failures += 1
        # without reading the mailbox we do not know whether a receipt is waiting: the
        # preventive reminders go on (they do not depend on a payment and have catch-up),
        # but collections and escalations stay on hold until the next good sweep
        summary.mailbox_unavailable = True
        summary.errors.append(f"ingestion: the mailbox sweep failed: {exc}")
        logger.exception(
            "The mailbox sweep failed; preventive reminders go on, collections and escalations are ON HOLD in this run"
        )

    installments = _collect_installments(settings, summary)
    targets = select_installments(
        installments, today, settings.days_before_due, settings.catchup_days
    )
    summary.selected = len(targets)
    logger.info("Today=%s | %d eligible installment(s)", today, len(targets))

    # the store keeps settlements, holds and the thread of each agreement: it serves the
    # reminder (reply in the conversation) and the collection cycle
    from settlement_reminders.ingest.store import AgreementStore

    store = AgreementStore(settings.history_db)
    contacted_today: set[str] = set()  # cases with an e-mail to the client in this run
    settled_cache: dict[str, set[str]] = {}
    for installment in targets:
        agreement = installment.agreement
        if history.already_sent(
            installment.external_id, due_date(installment), count_simulated=settings.dry_run
        ):
            summary.already_sent += 1
            logger.info("Skipped (already sent): %s", installment.external_id)
            continue
        if agreement.case_number not in settled_cache:
            settled_cache[agreement.case_number] = store.settled_installments(agreement.case_number)
        if installment.external_id in settled_cache[agreement.case_number]:
            summary.already_settled += 1
            logger.info("Skipped (already settled): %s", installment.external_id)
            continue

        to = [str(e) for e in agreement.client_emails]
        if not to:
            summary.no_recipient += 1
            msg = f"{installment.external_id}: agreement without a client e-mail"
            summary.errors.append(msg)
            logger.warning(msg)
            continue

        cc = _build_cc(
            settings,
            to,
            list(agreement.cc_emails)
            + list(settings.cc_notices)
            + settings.cc_for_payer(agreement.payer),
        )

        subject, body = render_reminder(installment)
        html, images = wrap_branded(
            body, signature=settings.signature_department, firm_name=settings.firm_name
        )
        try:
            _send_to_client(
                sender,
                agreement,
                store,
                settings,
                to=to,
                cc=cc,
                subject=subject,
                html=html,
                inline_images=images,
            )
        except Exception as exc:  # noqa: BLE001
            summary.failures += 1
            summary.errors.append(f"{installment.external_id}: {exc}")
            logger.exception("Failed to send %s", installment.external_id)
            history.record(
                run_day=today,
                external_id=installment.external_id,
                due=due_date(installment),
                case_number=agreement.case_number,
                client_email=", ".join(to),
                source="reminder",
                status="failed",
                error=str(exc),
            )
            continue

        status = "simulated" if settings.dry_run else "sent"
        summary.simulated += settings.dry_run
        summary.sent += not settings.dry_run
        history.record(
            run_day=today,
            external_id=installment.external_id,
            due=due_date(installment),
            case_number=agreement.case_number,
            client_email=", ".join(to),
            source="reminder",
            status=status,
        )
        contacted_today.add(agreement.case_number)

    # the collection cycle: overdue installments without a settlement (every source; the
    # settlements live in the same SQLite, so the routine also covers the spreadsheet
    # agreements)
    _collection_pass(
        installments,
        settings,
        sender,
        history,
        store,
        summary,
        today,
        already_contacted=contacted_today,
    )

    return summary


def pending_agreements(settings: Settings) -> list[str]:
    """Lines describing the pending agreements, for the digest."""
    if "notices" not in settings.sources:
        return []
    from settlement_reminders.ingest.store import AgreementStore

    return [
        f"{r.case_number}"
        + (f" [{r.origin}]" if r.origin else "")
        + (f": {'; '.join(r.issues)}" if r.issues else "")
        for r in AgreementStore(settings.history_db).list_agreements("pending")
    ]
