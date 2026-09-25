"""Rendering of the client-facing templates and sending through Microsoft Graph.

The templates are in Portuguese on purpose: they are the e-mails a Brazilian client
receives, faithful to the firm's standard (amounts spelled out, feminine ordinals, the
late-payment clause transcribed, never computed).
"""

from __future__ import annotations

import base64
import logging
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from settlement_reminders.business_days import next_business_day, weekday_name
from settlement_reminders.formatting import brl, date_br
from settlement_reminders.models import Installment
from settlement_reminders.spelled_out import amount_in_words, ordinal_feminine

logger = logging.getLogger(__name__)

NOTICE_SUBJECT_PREFIX = "ACORDO PACTUADO"
REMINDER_SUBJECT_PREFIX = "LEMBRETE DE PAGAMENTO"
COLLECTION_SUBJECT_PREFIX = "PAGAMENTO EM ABERTO"
TEST_MARKERS = ("[TEST]", "[TESTE]")


def is_test_subject(subject: str) -> bool:
    upper = (subject or "").upper()
    return any(marker in upper for marker in TEST_MARKERS)


@lru_cache(maxsize=1)
def _env() -> Environment:
    templates_path = Path(__file__).resolve().parent / "templates"
    return Environment(
        loader=FileSystemLoader(str(templates_path)), autoescape=select_autoescape(["html", "j2"])
    )


def _addressed_honorific(honorific: str) -> str:
    """'Sr.'/'Dr.' -> 'ao ...'; 'Sra.'/'Dra.' -> 'à ...'."""
    h = honorific.strip()
    if h.lower().startswith(("sra", "dra")):
        return f"à {h}"
    return f"ao {h}"


_ASSETS = Path(__file__).resolve().parent / "assets"
MARK_CID = "brand-mark"


@lru_cache(maxsize=1)
def brand_images() -> list[dict]:
    """The brand mark as an inline (CID) attachment; [] when the asset is missing.

    An image embedded by CID is the only reliable way to show a brand in classic Outlook
    (data: URIs and external images are blocked).
    """
    path = _ASSETS / "mark.png"
    if not path.exists():
        return []
    return [
        {
            "name": "mark.png",
            "contentType": "image/png",
            "contentId": MARK_CID,
            "content": path.read_bytes(),
        }
    ]


_CONFIDENTIALITY_FOOTER = (
    "<hr style='margin:24px 0 8px;border:none;border-top:1px solid #e4e3de' />"
    "<p style='font-size:11px;color:#8a8d93;line-height:1.4;margin:0'>"
    "ALERTA: Conteúdo confidencial privilegiado. Não utilize-o ou divulgue se você não for o "
    "destinatário.<br />NOTICE: Confidential or legally privileged content. Do not use or "
    "disclose it if you are not the intended recipient.</p>"
)


def wrap_branded(
    body_html: str, *, signature: str | None = None, firm_name: str = ""
) -> tuple[str, list[dict]]:
    """Institutional frame: header (mark + firm name), signature and confidentiality footer.
    Returns (html, inline_images).

    `signature`: the text after "Atenciosamente," (the DEPARTMENT, never a person's name);
    None when the body already carries its own signature (e.g. a notice written by the
    skill).
    """
    images = brand_images()
    header = ""
    if images:
        header = (
            "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='margin-bottom:8px'><tr>"
            f"<td align='left' style='vertical-align:middle'><img src='cid:{MARK_CID}' width='36' height='36' "
            f"alt='{escape(firm_name)}' style='display:block;border:0' /></td>"
            f"<td align='right' style='vertical-align:middle;font-size:13px;font-weight:bold;color:#17181c'>"
            f"{escape(firm_name)}</td>"
            "</tr></table>"
            "<hr style='margin:0 0 20px;border:none;border-top:2px solid #4d7c0f' />"
        )
    signature_block = f"<p>Atenciosamente,<br /><b>{escape(signature)}</b></p>" if signature else ""
    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#17181c;line-height:1.6;max-width:680px">'
        f"{header}{body_html}{signature_block}{_CONFIDENTIALITY_FOOTER}</div>"
    )
    return html, images


def _payment_details_html(installment: Installment) -> Markup:
    # escape the text and turn line breaks into <br/> (marked safe)
    return Markup(str(escape(installment.payment_details.strip())).replace("\n", "<br />\n"))


def _subject(prefix: str, installment: Installment) -> str:
    a = installment.agreement
    return f"{prefix} - {a.case_number} - {a.claimant.upper()} X {a.payer.upper()}"


def render_reminder(installment: Installment) -> tuple[str, str]:
    """Renders (subject, body_html) of the preventive reminder."""
    agreement = installment.agreement
    due = next_business_day(installment.original_due)
    postponed = due != installment.original_due
    clause = agreement.late_clause.strip().rstrip(".")

    html = (
        _env()
        .get_template("reminder.html.j2")
        .render(
            ordinal=ordinal_feminine(installment.number),
            is_last=installment.is_last,
            due_date=date_br(due),
            weekday=weekday_name(due),
            postponed=postponed,
            original_due=date_br(installment.original_due),
            original_weekday=weekday_name(installment.original_due),
            amount=brl(installment.amount).replace("R$ ", ""),
            amount_in_words=amount_in_words(installment.amount),
            addressed_honorific=_addressed_honorific(installment.honorific),
            beneficiary=installment.beneficiary_name.upper(),
            payment_method=installment.payment_method.strip(),
            payment_details_html=_payment_details_html(installment),
            late_clause=clause,
        )
    )
    subject = _subject(
        f"{REMINDER_SUBJECT_PREFIX} DA {installment.number}ª PARCELA DE ACORDO", installment
    )
    return subject, html


def render_collection(installment: Installment) -> tuple[str, str]:
    """Renders (subject, body_html) of the post-due collection.

    The tone asks for the receipt (the client may have paid without sending it); the
    late-payment clause is transcribed, never computed. The case number in the subject is
    what matches the replies.
    """
    agreement = installment.agreement
    due = next_business_day(installment.original_due)
    clause = agreement.late_clause.strip().rstrip(".")

    html = (
        _env()
        .get_template("collection.html.j2")
        .render(
            ordinal=ordinal_feminine(installment.number),
            is_last=installment.is_last,
            due_date=date_br(due),
            weekday=weekday_name(due),
            amount=brl(installment.amount).replace("R$ ", ""),
            amount_in_words=amount_in_words(installment.amount),
            addressed_honorific=_addressed_honorific(installment.honorific),
            beneficiary=installment.beneficiary_name.upper(),
            payment_method=installment.payment_method.strip(),
            payment_details_html=_payment_details_html(installment),
            late_clause=clause,
        )
    )
    subject = _subject(
        f"{COLLECTION_SUBJECT_PREFIX} - {installment.number}ª PARCELA DE ACORDO", installment
    )
    return subject, html


class EmailSender:
    """Sends e-mails through Microsoft Graph (or only simulates, in DRY_RUN)."""

    def __init__(self, graph, sender: str, sender_name: str = "", *, dry_run: bool = True) -> None:
        self.graph = graph
        self.sender = sender
        self.sender_name = sender_name
        self.dry_run = dry_run

    def send(
        self,
        *,
        to: list[str],
        subject: str,
        html: str,
        cc: list[str] | None = None,
        inline_images: list[dict] | None = None,
    ) -> None:
        """Sends an e-mail (several recipients + Cc). In DRY_RUN, only logs.

        `inline_images`: images embedded by CID (see `brand_images`), each one
        {name, contentType, contentId, content(bytes)}.
        """
        cc = cc or []
        if self.dry_run:
            logger.info("[DRY_RUN] e-mail NOT sent -> to=%s cc=%s | subject: %s", to, cc, subject)
            return
        if self.graph is None:
            raise RuntimeError("GraphClient missing for a real e-mail send.")
        message = self._payload(
            to=to, subject=subject, html=html, cc=cc, inline_images=inline_images
        )
        self.graph.post(
            f"/users/{self.sender}/sendMail", json={"message": message, "saveToSentItems": True}
        )
        logger.info("e-mail sent -> to=%s cc=%s | subject: %s", to, cc, subject)

    # ---- one thread per agreement ----------------------------------------------------
    # The notice opens the conversation; reminders and collections REPLY in it. Replying
    # requires creating a draft (Mail.ReadWrite). Without the permission everything below
    # falls back to a new message by itself: a send never fails because of the thread.

    _no_readwrite = False  # remembered after the first 403, so the attempt is not repeated

    @staticmethod
    def _recipients(addresses: list[str]) -> list[dict]:
        return [{"emailAddress": {"address": a}} for a in addresses]

    @staticmethod
    def _inline_attachments(inline_images: list[dict] | None) -> list[dict]:
        return [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": img["name"],
                "contentType": img["contentType"],
                "contentId": img["contentId"],
                "isInline": True,
                "contentBytes": base64.standard_b64encode(img["content"]).decode("ascii"),
            }
            for img in (inline_images or [])
        ]

    def _payload(
        self,
        *,
        to: list[str],
        subject: str,
        html: str,
        cc: list[str],
        inline_images: list[dict] | None,
    ) -> dict:
        message: dict = {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html},
            "toRecipients": self._recipients(to),
        }
        if cc:
            message["ccRecipients"] = self._recipients(cc)
        if inline_images:
            message["attachments"] = self._inline_attachments(inline_images)
        return message

    @staticmethod
    def _no_permission(exc: Exception) -> bool:
        text = str(exc)
        return "403" in text or "ErrorAccessDenied" in text

    def send_tracked(
        self,
        *,
        to: list[str],
        subject: str,
        html: str,
        cc: list[str] | None = None,
        inline_images: list[dict] | None = None,
    ) -> str | None:
        """Like send(), but returns the conversationId: opens the agreement's thread.

        Creates the draft (POST /messages, which already returns the conversation) and
        sends it. Without Mail.ReadWrite, sends like send() and returns None.
        """
        from settlement_reminders.graph import GraphError

        cc = cc or []
        if self.dry_run:
            self.send(to=to, cc=cc, subject=subject, html=html, inline_images=inline_images)
            return None
        if self.graph is None:
            raise RuntimeError("GraphClient missing for a real e-mail send.")
        if self._no_readwrite:
            self.send(to=to, cc=cc, subject=subject, html=html, inline_images=inline_images)
            return None

        payload = self._payload(
            to=to, subject=subject, html=html, cc=cc, inline_images=inline_images
        )
        try:
            draft = self.graph.post(f"/users/{self.sender}/messages", json=payload).json()
        except GraphError as exc:
            if not self._no_permission(exc):
                raise
            self._no_readwrite = True
            logger.warning(
                "No Mail.ReadWrite on the Graph app: the notice goes out without tracking the thread"
                " (reminders/collections will go as new messages)."
            )
            self.send(to=to, cc=cc, subject=subject, html=html, inline_images=inline_images)
            return None
        self.graph.post(f"/users/{self.sender}/messages/{draft['id']}/send")
        conversation = draft.get("conversationId")
        logger.info(
            "e-mail sent (thread %s) -> to=%s cc=%s | subject: %s",
            (conversation or "?")[-10:],
            to,
            cc,
            subject,
        )
        return conversation

    def find_thread(self, case_number: str) -> str | None:
        """conversationId of the notice 'ACORDO PACTUADO - {case}' already sent.

        Reads the Sent Items (Mail.Read is enough): links agreements whose notice went out
        before this feature. Test rehearsals do not count; the oldest notice is the one
        that opened the conversation.
        """
        from settlement_reminders.graph import GraphError

        graph = getattr(self, "graph", None)
        if graph is None:
            return None
        try:
            response = graph.get(
                f"/users/{self.sender}/mailFolders/sentitems/messages",
                params={
                    "$filter": f"startswith(subject, '{NOTICE_SUBJECT_PREFIX} - {case_number}')",
                    "$select": "subject,conversationId,sentDateTime",
                    "$top": "20",
                },
            )
        except GraphError as exc:
            logger.warning("Could not locate the thread of %s: %s", case_number, exc)
            return None
        items = [
            m
            for m in response.get("value", [])
            if not is_test_subject(m.get("subject") or "") and m.get("conversationId")
        ]
        if not items:
            return None
        return min(items, key=lambda m: m.get("sentDateTime") or "")["conversationId"]

    def reply_in_thread(
        self,
        *,
        conversation_id: str,
        to: list[str],
        subject: str,
        html: str,
        cc: list[str] | None = None,
        inline_images: list[dict] | None = None,
    ) -> bool:
        """Sends as a REPLY in the agreement's conversation. True when it entered the thread.

        Replies to the most recent message of the conversation (as a human would), keeping
        OUR subject: Outlook groups by conversation and the subject keeps saying what the
        e-mail is (reminder, collection). Any failure on the thread falls back to a new
        message and returns False.
        """
        from settlement_reminders.graph import GraphError

        cc = cc or []
        if self.dry_run:
            logger.info(
                "[DRY_RUN] e-mail NOT sent (in thread %s) -> to=%s cc=%s | subject: %s",
                conversation_id[-10:],
                to,
                cc,
                subject,
            )
            return True
        if self.graph is None:
            raise RuntimeError("GraphClient missing for a real e-mail send.")
        if self._no_readwrite or not conversation_id:
            self.send(to=to, cc=cc, subject=subject, html=html, inline_images=inline_images)
            return False

        base = f"/users/{self.sender}/messages"
        try:
            conversation = self.graph.get(
                base,
                params={
                    "$filter": f"conversationId eq '{conversation_id}'",
                    "$select": "id,sentDateTime,receivedDateTime",
                    "$top": "50",
                },
            ).get("value", [])
            if not conversation:
                raise GraphError(f"conversation {conversation_id[-10:]} has no messages")
            parent = max(
                conversation, key=lambda m: m.get("receivedDateTime") or m.get("sentDateTime") or ""
            )
            draft = self.graph.post(f"{base}/{parent['id']}/createReply").json()
            rid = draft["id"]
            self.graph.patch(
                f"{base}/{rid}",
                json={
                    "subject": subject,
                    "body": {"contentType": "HTML", "content": html},
                    "toRecipients": self._recipients(to),
                    "ccRecipients": self._recipients(cc),
                },
            )
            for attachment in self._inline_attachments(inline_images):
                self.graph.post(f"{base}/{rid}/attachments", json=attachment)
            self.graph.post(f"{base}/{rid}/send")
        except GraphError as exc:
            if self._no_permission(exc):
                self._no_readwrite = True
                logger.warning(
                    "No Mail.ReadWrite on the Graph app: reminders/collections go as new messages until it is granted."
                )
            else:
                logger.warning(
                    "Could not reply in the thread (%s); sending as a new message. Reason: %s",
                    subject,
                    str(exc)[:200],
                )
            self.send(to=to, cc=cc, subject=subject, html=html, inline_images=inline_images)
            return False
        logger.info(
            "e-mail sent IN THREAD %s -> to=%s cc=%s | subject: %s",
            conversation_id[-10:],
            to,
            cc,
            subject,
        )
        return True
