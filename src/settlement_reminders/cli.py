"""Command lines.

settlement-reminders [--date YYYY-MM-DD] [--demo]     the daily routine
settlement-agreements <command> ...                    manage agreements and installments
settlement-audit [--all]                               audit the real sends against the store
preview-email [reminder|collection] [--out FILE]        render a template with sample data
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from settlement_reminders import __version__


def _utf8_console() -> None:
    """Windows consoles default to a legacy code page; the subjects carry accents."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower() != "utf-8":
            stream.reconfigure(encoding="utf-8")


def _settings(demo: bool, reference: str | None):
    if demo:
        os.environ["DEMO_MODE"] = "true"
    if reference:
        os.environ["REFERENCE_DATE"] = reference
    from settlement_reminders.config import get_settings

    return get_settings()


# ---------------------------------------------------------------- daily run -------------


def run_main() -> int:
    parser = argparse.ArgumentParser(
        prog="settlement-reminders", description="Runs the daily routine once."
    )
    parser.add_argument(
        "--date", metavar="YYYY-MM-DD", help="pin 'today' (the demo defaults to 2026-09-17)"
    )
    parser.add_argument(
        "--demo", action="store_true", help="DEMO_MODE=true: fixtures, outbox, no network"
    )
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()

    _utf8_console()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = _settings(args.demo, args.date)

    from settlement_reminders.factory import build_history, build_ingest, build_sender
    from settlement_reminders.notifier import alert_exceptions, alert_failure
    from settlement_reminders.run import execute, pending_agreements

    logger = logging.getLogger("settlement_reminders")
    sender = build_sender(settings)
    history = build_history(settings)

    if settings.demo_mode:
        logger.warning(
            "DEMO_MODE: fixture mailbox, recorded readings, e-mails written to %s",
            Path(settings.history_db).parent / "outbox",
        )
    elif settings.dry_run:
        logger.warning("DRY_RUN on: no e-mail will really be sent.")

    try:
        summary = execute(settings, sender, history, ingest=build_ingest(settings, sender))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Fatal failure in the reminder routine")
        try:
            alert_failure(sender, settings.alert_emails, str(exc))
        except Exception:  # noqa: BLE001
            logger.exception("Also failed to alert the team")
        return 1

    logger.info("Summary: %s", summary)
    _print_summary(settings, summary)

    pending = pending_agreements(settings)
    if (
        pending
        or summary.collections_held
        or summary.replies_to_check
        or summary.replies_closed
        or summary.replies_settled
        or summary.escalated
        or summary.errors
    ):
        alert_exceptions(
            sender,
            settings.alert_emails,
            pending=pending,
            to_check=summary.replies_to_check,
            closed=summary.replies_closed,
            settled=summary.replies_settled,
            escalated=summary.escalated,
            errors=summary.errors,
            held=summary.collections_held,
        )
    return 0


def _print_summary(settings, summary) -> None:
    from settlement_reminders.rules import today_in_tz

    today = today_in_tz(settings.timezone, settings.reference_date)
    mode = "DEMO" if settings.demo_mode else ("DRY_RUN" if settings.dry_run else "LIVE")
    print(
        f"Run of {today:%Y-%m-%d} ({today:%A}) | mode: {mode} | sources: {', '.join(settings.sources)}"
    )
    print(f"Agreements: {summary.total_agreements} | installments: {summary.total_installments}")
    print(
        f"Ingested: {summary.ingested} notice(s)/draft(s) | "
        f"settled by receipt: {len(summary.replies_settled)} | to check: {len(summary.replies_to_check)}"
    )
    print(
        f"Reminders: selected {summary.selected} | sent {summary.sent} | simulated {summary.simulated} |"
        f" already sent {summary.already_sent} | already settled {summary.already_settled}"
    )
    print(
        f"Collections: sent {summary.collections_sent} | simulated {summary.collections_simulated} | "
        f"on hold {len(summary.collections_held)} | escalated {len(summary.escalated)}"
    )
    for line in summary.replies_settled:
        print(f"  settled:   {line}")
    for line in summary.replies_to_check:
        print(f"  check:     {line}")
    for line in summary.collections_held:
        print(f"  on hold:   {line}")
    for line in summary.escalated:
        print(f"  escalated: {line}")
    for line in summary.errors:
        print(f"  error:     {line}")
    sent = getattr(getattr(summary, "_sender", None), "sent", None)
    if sent:
        for item in sent:
            print(f"  outbox: {item['file']}")


# ------------------------------------------------------------ management --------------

_COMMANDS = (
    "list",
    "installments",
    "settle",
    "unsettle",
    "close",
    "reopen",
    "set-emails",
    "holds",
    "release-hold",
    "link-threads",
)

_MANAGE_DOC = """\
settlement-agreements list [active|pending|closed]
settlement-agreements installments <case>
settlement-agreements settle <case> O1P2 ["reason"]
settlement-agreements unsettle <case> O1P2
settlement-agreements close <case> ["reason"]
settlement-agreements reopen <case>
settlement-agreements set-emails <case> "a@x.com,b@y.com" ["reason"] [lawyer@firm]
settlement-agreements holds
settlement-agreements release-hold <case>
settlement-agreements link-threads

`settle` marks ONE installment as paid (its collection stops); when every installment is
settled the agreement is closed. `set-emails` is an OPERATOR override: it redefines the
recipients of the cycle and activates the agreement when nothing else is pending. `holds`
lists the agreements with the collection ON HOLD (the client replied with an attachment that
did not settle); after checking, `settle` the paid installment or `release-hold` when it was
not a payment. Operates on HISTORY_DB (.env), or on the demo database with --demo.
"""


def manage_main() -> int:
    _utf8_console()
    argv = list(sys.argv[1:])
    demo = "--demo" in argv
    argv = [a for a in argv if a != "--demo"]
    if not argv or argv[0] not in _COMMANDS:
        print(_MANAGE_DOC)
        return 2

    settings = _settings(demo, None)
    from settlement_reminders.ingest import AgreementStore

    store = AgreementStore(settings.history_db)
    command = argv[0]

    if command == "list":
        return _list(store, argv[1] if len(argv) > 1 else None)

    if command == "holds":
        items = store.holds()
        if not items:
            print("No collection on hold.")
            return 0
        for case, reason, since in items:
            print(f"[ON HOLD since {since[:10]}] {case}\n            {reason}")
        print(f"\n{len(items)} agreement(s) without collection until a human releases them.")
        return 0

    if command == "link-threads":
        return _link_threads(settings, store)

    if len(argv) < 2:
        print(_MANAGE_DOC)
        return 2
    case = argv[1]

    if command == "installments":
        return _installments(store, case)

    if command == "settle":
        if len(argv) < 3:
            print(_MANAGE_DOC)
            return 2
        external_id = f"{case}#{argv[2].upper()}"
        reason = argv[3] if len(argv) > 3 else "manual settlement"
        installments = {i.external_id for i in store.installments_of(case)}
        if external_id not in installments:
            print(f"Installment not found in the active agreement: {external_id}")
            print("Use 'installments' to see the identifiers (e.g. O1P2).")
            return 1
        if not store.settle_installment(external_id, case, "manual", reason):
            print(f"Installment was already settled: {external_id}")
            return 1
        print(f"Settled: {external_id}")
        remaining = installments - store.settled_installments(case)
        if not remaining:
            store.close(case, "every installment settled (manual settlement)")
            print("Every installment settled: agreement closed.")
        else:
            print(f"{len(remaining)} installment(s) still open.")
        return 0

    if command == "unsettle":
        if len(argv) < 3:
            print(_MANAGE_DOC)
            return 2
        external_id = f"{case}#{argv[2].upper()}"
        ok = store.unsettle(external_id)
        print("Settlement reverted." if ok else f"Settlement not found: {external_id}")
        return 0 if ok else 1

    if command == "release-hold":
        ok = store.release_collection(case)
        print(
            "Collection released (back in the cycle on the next run)."
            if ok
            else f"There was no hold for {case}."
        )
        return 0 if ok else 1

    if command == "set-emails":
        if len(argv) < 3:
            print(_MANAGE_DOC)
            return 2
        emails = [e.strip() for e in argv[2].replace(";", ",").split(",") if e.strip()]
        reason = argv[3] if len(argv) > 3 else ""
        lawyer = argv[4] if len(argv) > 4 else None
        status = store.set_emails(case, emails, reason=reason, lawyer=lawyer)
        if status is None:
            print(f"Case not found (or closed): {case}")
            return 1
        print(f"Recipients: {', '.join(emails)}")
        print(f"Agreement status: {status}")
        return 0

    if command == "close":
        reason = argv[2] if len(argv) > 2 else "manual closing"
        ok = store.close(case, f"manual: {reason}")
        print("Closed." if ok else f"Case not found: {case}")
        return 0 if ok else 1

    ok = store.reopen(case)
    print("Reopened." if ok else f"Case not found: {case}")
    return 0 if ok else 1


def _list(store, status: str | None) -> int:
    records = store.list_agreements(status)
    if not records:
        print(f"No agreement{f' with status {status!r}' if status else ''}.")
        return 0
    for r in records:
        settled = store.settled_installments(r.case_number)
        extra = f"  settled={len(settled)}" if settled else ""
        print(f"[{r.status:8}] {r.case_number}  recipient={r.match_status}{extra}")
        if r.client_emails:
            print(f"           e-mails: {', '.join(r.client_emails)}")
        if r.origin:
            print(f"           origin:  {r.origin}")
        for i in r.issues:
            print(f"           issue:   {i}")
        if r.closed_reason:
            print(f"           closed:  {r.closed_reason}")
    print(f"\nTotal: {len(records)}")
    return 0


def _installments(store, case: str) -> int:
    installments = store.installments_of(case)
    if not installments:
        record = store.find(case)
        state = record.status if record else "nonexistent"
        print(f"No active installments for {case} (agreement {state}).")
        return 1
    settled = store.settled_installments(case)
    for i in installments:
        marker = "PAID " if i.external_id in settled else "OPEN "
        suffix = i.external_id.split("#", 1)[1]
        print(
            f"[{marker}] {suffix:8} installment {i.number:2}  due {i.original_due:%d/%m/%Y}  "
            f"R$ {i.amount}  -> {i.beneficiary_name}"
        )
    print(f"\n{len(settled)}/{len(installments)} settled.")
    return 0


def _link_threads(settings, store) -> int:
    """Links each active agreement to the conversation of its notice (one thread per agreement)."""
    pending = store.without_thread()
    if not pending:
        print("Every active agreement already has the notice's conversation linked.")
        return 0
    if settings.demo_mode:
        print(
            f"{len(pending)} agreement(s) without a thread; the demo has no Sent Items to search."
        )
        return 1
    from settlement_reminders.graph import GraphClient
    from settlement_reminders.mailer import EmailSender

    sender = EmailSender(
        GraphClient(settings.ms_tenant_id, settings.ms_client_id, settings.ms_client_secret),
        settings.ms_sender,
        settings.display_name,
        dry_run=False,
    )
    missing = 0
    for case in pending:
        conversation = sender.find_thread(case)
        if conversation:
            store.set_thread(case, conversation)
            print(f"[ok]   {case} -> conversation ...{conversation[-10:]}")
        else:
            missing += 1
            print(f"[none] {case} -> notice not found in the Sent Items")
    print(f"\n{len(pending) - missing} linked, {missing} without a locatable notice.")
    return 1 if missing else 0


# ------------------------------------------------------------------ audit --------------


def audit_main() -> int:
    """Audits the REAL sends: what went out, to whom, and whether it was right.

    Reads the `sends` table (sent/failed) and crosses each send with the agreement's state
    in the store. Flags anomalies: recipients different from the registered ones; a send for
    an agreement absent from the store (unless a spreadsheet source is configured); a send
    for a closed agreement or for an installment already settled; the same collection attempt
    recorded twice; more than one collection of the same agreement on the same day; failed
    sends. Keeps the pointer of the last audit next to the database.
    """
    _utf8_console()
    argv = sys.argv[1:]
    settings = _settings("--demo" in argv, None)
    everything = "--all" in argv

    from settlement_reminders.history import SendHistory
    from settlement_reminders.ingest import AgreementStore

    marker = Path(settings.history_db).parent / ".last-audit"
    since = "1970-01-01"
    if not everything and marker.exists():
        since = marker.read_text().strip()

    history = SendHistory(settings.history_db)
    store = AgreementStore(settings.history_db)
    sends = history.real_sends(since)
    problems: list[str] = []

    if not sends:
        print(f"No real send since {since}. Nothing to audit.")
    else:
        print(f"{len(sends)} real send(s) since {since}:\n")

    seen: set[str] = set()
    collections_per_day: dict[tuple[str, str], int] = {}
    spreadsheet_sources = bool({"excel", "sharepoint"} & set(settings.sources))
    for s in sends:
        idx = s["external_id"]
        case = s["case_number"] or ""
        day = s["run_day"] or (s["sent_at"] or "")[:10]
        kind = s["source"] or "?"
        dest = s["client_email"] or ""
        print(f"[{s['status']:9}] {s['sent_at'][:16]} {kind:10} {idx}")
        print(f"            -> {dest}")

        if s["status"] == "failed":
            problems.append(f"FAILED send: {idx} ({s['error']})")
            continue

        key = f"{idx}|{s['due_date']}"
        if key in seen:
            problems.append(f"DUPLICATE: {idx} recorded twice for the same due date")
        seen.add(key)

        if kind == "collection":
            collections_per_day[(case, day)] = collections_per_day.get((case, day), 0) + 1
            if collections_per_day[(case, day)] > 1:
                problems.append(
                    f"RAMP VIOLATED: {case} received {collections_per_day[(case, day)]} collections on {day}"
                )

        record = store.find(case) if case else None
        if record is None:
            if kind in ("reminder", "collection") and not spreadsheet_sources:
                problems.append(f"NO AGREEMENT in the store: {idx}")
            elif kind in ("reminder", "collection"):
                print("            (not in the store: a spreadsheet/SharePoint agreement)")
            continue
        if kind in ("reminder", "collection"):
            expected = {x.lower() for x in record.client_emails}
            sent_to = {x.strip().lower() for x in dest.split(",") if x.strip()}
            if expected and sent_to - expected:
                problems.append(
                    f"UNEXPECTED RECIPIENT in {idx}: {', '.join(sorted(sent_to - expected))}"
                )
            if record.status == "closed" and kind == "collection":
                problems.append(
                    f"ATTENTION: collection {idx} of an agreement now closed (fine if closed after the send)"
                )
            base = idx.split("#C")[0]
            if base in store.settled_installments(case) and kind == "collection":
                problems.append(
                    f"ATTENTION: collection {idx} of an installment now settled (fine if settled after the send)"
                )

    print()
    if problems:
        print(f"! {len(problems)} PROBLEM(S):")
        for p in problems:
            print(f"  - {p}")
    else:
        print("OK: no anomaly in the audited sends.")

    if not everything:
        from datetime import UTC, datetime

        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(datetime.now(UTC).isoformat())
    return 1 if problems else 0


# ---------------------------------------------------------------- preview --------------


def preview_main() -> int:
    """Renders a template with sample data into an HTML file (opens in a browser)."""
    _utf8_console()
    parser = argparse.ArgumentParser(prog="preview-email")
    parser.add_argument("kind", nargs="?", choices=["reminder", "collection"], default="reminder")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    from settlement_reminders.mailer import render_collection, render_reminder, wrap_branded
    from settlement_reminders.models import AgreementBase, Installment

    agreement = AgreementBase(
        case_number="0001234-74.2099.5.99.0001",
        claimant="Maria Souza (fictional)",
        payer="Empresa Exemplo LTDA (fictional)",
        client_emails="finance@client.example",
        late_clause=(
            "em caso de atraso no pagamento da parcela, haverá incidência de multa de 50% (cinquenta por cento),"
            " aplicável a partir do 5º (quinto) dia de mora"
        ),
    )
    installment = Installment(
        agreement=agreement,
        number=7,
        total=7,
        original_due=date(2026, 12, 12),
        amount=Decimal("4800.00"),
        beneficiary_name="Maria Souza",
        honorific="Sra.",
        payment_method="transferência bancária",
        payment_details="Titular: Maria Souza;\nCPF: 000.000.000-00;\nBanco: 000 - Banco Exemplo;\n"
        "Agência: 0000;\nConta Corrente: 00000-0.",
        is_last=True,
        obligation_id=1,
    )
    subject, body = (render_collection if args.kind == "collection" else render_reminder)(
        installment
    )
    html, images = wrap_branded(
        body, signature="Controladoria Jurídica", firm_name="Example Law Firm"
    )
    import base64

    for img in images:  # inline the CID image so the file opens in a browser
        html = html.replace(
            f"cid:{img['contentId']}",
            f"data:{img['contentType']};base64,{base64.b64encode(img['content']).decode()}",
        )
    out = Path(args.out or f".demo/preview-{args.kind}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Subject: {subject}")
    print(f"Preview written to {out}")
    return 0
