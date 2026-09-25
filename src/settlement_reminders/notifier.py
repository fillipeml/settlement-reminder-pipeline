"""Alerts to the team (by e-mail): a failed run and the daily exceptions digest."""

from __future__ import annotations

import logging
from html import escape

logger = logging.getLogger(__name__)


def alert_failure(sender, recipients: list[str], error: str) -> None:
    """E-mails the team when the routine fails."""
    if not recipients:
        logger.warning("The run failed but ALERT_EMAILS is not set: %s", error)
        return
    subject = "[ALERT] Settlement reminder run failed"
    html = (
        "<p>The settlement reminder routine failed.</p>"
        f"<pre style='background:#f4f5f7;padding:12px;border-radius:6px'>{escape(error)}</pre>"
        "<p>Check the run's log.</p>"
    )
    try:
        sender.send(to=list(recipients), subject=subject, html=html)
    except Exception:  # noqa: BLE001 - an alert must never bring the process down
        logger.exception("Failed to send the alert to %s", recipients)


def _section(title: str, items: list[str]) -> str:
    if not items:
        return ""
    lis = "\n".join(f"<li>{escape(item)}</li>" for item in items)
    return f"<h3 style='margin:16px 0 4px'>{escape(title)}</h3>\n<ul>\n{lis}\n</ul>"


def render_exceptions_digest(
    *,
    pending: list[str],
    to_check: list[str],
    closed: list[str],
    errors: list[str],
    settled: list[str] | None = None,
    escalated: list[str] | None = None,
    held: list[str] | None = None,
) -> tuple[str, str]:
    """Builds (subject, html) of the daily exceptions digest."""
    settled = settled or []
    escalated = escalated or []
    held = held or []
    parts = []
    if held:
        parts.append(f"{len(held)} collection(s) ON HOLD")
    if pending:
        parts.append(f"{len(pending)} pending agreement(s)")
    if to_check:
        parts.append(f"{len(to_check)} to check")
    if escalated:
        parts.append(f"{len(escalated)} escalation(s)")
    if settled:
        parts.append(f"{len(settled)} settlement(s) by receipt")
    if closed:
        parts.append(f"{len(closed)} agreement(s) closed")
    if errors:
        parts.append(f"{len(errors)} error(s)")
    subject = "[REMINDERS] Exceptions digest - " + ", ".join(parts)

    html = (
        "<p>Digest of the settlement reminder routine. Only the items below need a human;"
        " everything else ran automatically.</p>"
        + _section(
            "Collections ON HOLD (the client replied with an attachment that did not settle"
            " an installment: check the payment; release with 'settle' or 'release-hold')."
            " Until then no collection and no escalation goes out.",
            held,
        )
        + _section("Pending agreements (NOT in the automatic cycle until resolved)", pending)
        + _section("Replies to check (nothing was settled or closed)", to_check)
        + _section(
            "ESCALATED installments (collection cap reached; the contact is now human)", escalated
        )
        + _section("Installments settled by receipt (for awareness)", settled)
        + _section("Agreements closed (every installment settled)", closed)
        + _section("Errors of the run", errors)
        + "<p>The full history is in the automation's database.</p>"
    )
    return subject, html


def alert_exceptions(
    sender,
    recipients: list[str],
    *,
    pending: list[str],
    to_check: list[str],
    closed: list[str],
    errors: list[str],
    settled: list[str] | None = None,
    escalated: list[str] | None = None,
    held: list[str] | None = None,
) -> None:
    """Sends the exceptions digest (when there are recipients)."""
    if not recipients:
        logger.warning(
            "Exceptions to report but ALERT_EMAILS is not set: %d pending, %d to check, %d closed, %d error(s)",
            len(pending),
            len(to_check),
            len(closed),
            len(errors),
        )
        return
    subject, html = render_exceptions_digest(
        pending=pending,
        to_check=to_check,
        closed=closed,
        errors=errors,
        settled=settled,
        escalated=escalated,
        held=held,
    )
    try:
        sender.send(to=list(recipients), subject=subject, html=html)
    except Exception:  # noqa: BLE001 - an alert must never bring the process down
        logger.exception("Failed to send the exceptions digest to %s", recipients)
