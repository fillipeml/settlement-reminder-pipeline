"""Parser of the settlement notice written by the skill (the AUTOMATION-DATA block).

The `settlement-notice` skill (skills/settlement-notice/SKILL.md) writes the firm's standard
notice to the client and appends a machine-readable block at the end. This module turns that
block into an `ExtractedAgreement`, the same schema as the model extraction, so the rest of
the pipeline (store, convert, rules, sending) works unchanged. The reading is deterministic:
no API call, zero cost.

Error policy (mirrors the project's conservative policy):

- a STRUCTURAL problem (block missing/corrupted, required key missing, invalid format) ->
  `InvalidNoticeError`; the message becomes an item for human checking, nothing is registered;
- a SOFT problem (sum does not match, dates out of order, an internal e-mail in the client
  list, lawyer missing) -> becomes a `doubt` and lowers the confidence to "medium": the
  agreement is born PENDING and appears in the exceptions digest.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from settlement_reminders.ingest.schema import (
    ExtractedAgreement,
    ExtractedInstallment,
    PaymentObligation,
)

logger = logging.getLogger(__name__)

SUPPORTED_VERSION = 1
DEFAULT_INTERNAL_DOMAIN = "lawfirm.example"

_BLOCK_RE = re.compile(
    r"===\s*AUTOMATION-DATA\s+v(?P<version>\d+)\s*===\s*(?P<body>.*?)\s*===\s*END\s+AUTOMATION-DATA\s*===",
    re.DOTALL | re.IGNORECASE,
)
_KEY_LINE_RE = re.compile(r"^([A-Z_]+):\s*(.*)$")
CASE_NUMBER_RE = re.compile(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# structural line breaks of the HTML are preserved before removing tags (the block travels
# inside <pre>, but the body may arrive reformatted)
_BREAK_RE = re.compile(r"<\s*(?:br\s*/?|/p|/div|/li|/tr|/pre|/h[1-6])\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")

_UNIQUE_KEYS = {
    "OPERATION",
    "CASE",
    "CLIENT",
    "CLIENT_SIDE",
    "COUNTERPARTY",
    "CLIENT_EMAILS",
    "LAWYER",
    "COURT",
    "TOTAL_AMOUNT",
    "LATE_CLAUSE",
    "CC_EXTRA",
}
_REPEATABLE_KEYS = {"OBLIGATION", "PAYMENT", "INSTALLMENT", "NO_COLLECTION", "DUTY"}


class InvalidNoticeError(ValueError):
    """AUTOMATION-DATA block missing or structurally invalid."""


@dataclass
class ParsedNotice:
    """The result of reading a settlement notice."""

    operation: str  # REGISTER | CORRECTION
    agreement: ExtractedAgreement
    client_emails: list[str]
    lawyer: str = ""
    client_side: str = ""
    court: str = ""
    duties: list[str] = field(default_factory=list)
    # extra copies of reminders/collections (besides the controllership) indicated by the
    # lawyer, internal or external
    cc_extra: list[str] = field(default_factory=list)


def html_to_text(content: str) -> str:
    """Converts an HTML body into plain text; plain text passes untouched."""
    text = _BREAK_RE.sub("\n", content)
    text = _TAG_RE.sub("", text)
    return html_lib.unescape(text)


def has_data_block(content: str) -> bool:
    """True when the content (HTML or text) carries an AUTOMATION-DATA block."""
    return _BLOCK_RE.search(html_to_text(content)) is not None


_RAW_BLOCK_RE = re.compile(
    r"(?:<pre[^>]*>\s*)?===\s*AUTOMATION-DATA.*?===\s*END\s+AUTOMATION-DATA\s*===\s*(?:</pre>)?",
    re.DOTALL | re.IGNORECASE,
)
_MARKER_RE = re.compile(
    r"(?:<p[^>]*>\s*)?[—-]+\s*automation data[^<\n—-]*[—-]+\s*(?:</p>)?",
    re.IGNORECASE,
)


def strip_data_block(content: str) -> str:
    """The client's version of the notice: without the machine block.

    Removes the AUTOMATION-DATA block (and the `<pre>` around it) and the "automation data,
    internal use" marker line from the original HTML/text, keeping the rest of the notice
    as the lawyer approved it.
    """
    without_block = _RAW_BLOCK_RE.sub("", content)
    return _MARKER_RE.sub("", without_block).strip()


def _first_token(value: str) -> str:
    parts = value.split()
    return parts[0] if parts else ""


_AMOUNT_BR_RE = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{1,2}$|^\d+,\d{1,2}$")
_DATE_BR_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


def _parse_amount(text: str, context: str) -> Decimal:
    """Decimal from '2119.17' (the spec) or '2.119,17'/'2119,17' (a Brazilian slip)."""
    raw = text.strip()
    if _AMOUNT_BR_RE.match(raw):
        raw = raw.replace(".", "").replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise InvalidNoticeError(f"{context}: invalid amount {text!r}") from exc


def _parse_date(text: str, context: str) -> date:
    """ISO date (the spec) or DD/MM/YYYY (a Brazilian slip)."""
    raw = text.strip()
    if m := _DATE_BR_RE.match(raw):
        raw = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise InvalidNoticeError(f"{context}: invalid date {text!r}") from exc


def _collect_lines(body: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Splits the block body into unique and repeatable keys.

    A line that does not start with `KEY:` is treated as a continuation of the previous key
    (mail clients may rewrap long lines, e.g. LATE_CLAUSE).
    """
    unique: dict[str, str] = {}
    repeatable: dict[str, list[str]] = {k: [] for k in _REPEATABLE_KEYS}
    last: tuple[str, int] | None = None  # (key, index in the list) for continuations

    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _KEY_LINE_RE.match(line)
        if not m:
            if last is None:
                raise InvalidNoticeError(f"unexpected line before the first key: {line!r}")
            key, idx = last
            if idx < 0:
                unique[key] = f"{unique[key]} {line}"
            else:
                repeatable[key][idx] = f"{repeatable[key][idx]} {line}"
            continue

        key, value = m.group(1), m.group(2).strip()
        if key in _UNIQUE_KEYS:
            if key in unique:
                raise InvalidNoticeError(f"duplicated key in the block: {key}")
            unique[key] = value
            last = (key, -1)
        elif key in _REPEATABLE_KEYS:
            repeatable[key].append(value)
            last = (key, len(repeatable[key]) - 1)
        else:
            logger.warning("Unknown key in the AUTOMATION-DATA block: %s", key)
            last = None
    return unique, repeatable


_HONORIFICS = {"sr": "Sr.", "sra": "Sra.", "dr": "Dr.", "dra": "Dra."}


def _normalise_honorific(value: str) -> str:
    v = value.strip().rstrip(".").lower()
    if v in _HONORIFICS:
        return _HONORIFICS[v]
    raise InvalidNoticeError(f"invalid honorific (expected Sr./Sra./Dr./Dra.): {value!r}")


def _parse_obligations(repeatable: dict[str, list[str]]) -> list[PaymentObligation]:
    """Builds the obligations from the OBLIGATION/PAYMENT/INSTALLMENT lines."""
    descriptions: dict[int, dict[str, str]] = {}
    payments: dict[int, tuple[str, str]] = {}
    installments: dict[int, list[ExtractedInstallment]] = {}

    for line in repeatable["OBLIGATION"]:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            raise InvalidNoticeError(f"malformed OBLIGATION line: {line!r}")
        try:
            num = int(parts[0])
        except ValueError as exc:
            raise InvalidNoticeError(f"invalid obligation number: {line!r}") from exc
        if num in descriptions:
            raise InvalidNoticeError(f"obligation {num} declared twice")
        fields = {"description": parts[1]}
        for extra in parts[2:]:
            key, _, value = extra.partition("=")
            fields[key.strip().lower()] = value.strip()
        if not fields.get("beneficiary"):
            raise InvalidNoticeError(f"obligation {num} without a beneficiary")
        descriptions[num] = fields

    for line in repeatable["PAYMENT"]:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            raise InvalidNoticeError(f"malformed PAYMENT line: {line!r}")
        try:
            num = int(parts[0])
        except ValueError as exc:
            raise InvalidNoticeError(f"invalid obligation number: {line!r}") from exc
        if num in payments:
            raise InvalidNoticeError(f"payment of obligation {num} declared twice")
        payments[num] = (parts[1], " | ".join(parts[2:]))

    for line in repeatable["INSTALLMENT"]:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 4:
            raise InvalidNoticeError(f"malformed INSTALLMENT line: {line!r}")
        try:
            obligation = int(parts[0])
            number = int(parts[1])
        except ValueError as exc:
            raise InvalidNoticeError(f"invalid INSTALLMENT line: {line!r}") from exc
        due = _parse_date(parts[2], f"INSTALLMENT {obligation}|{number}")
        amount = _parse_amount(parts[3], f"INSTALLMENT {obligation}|{number}")
        if amount <= 0:
            raise InvalidNoticeError(f"installment with a non-positive amount: {line!r}")
        installments.setdefault(obligation, []).append(
            ExtractedInstallment(number=number, due=due, amount=amount)
        )

    if not descriptions:
        raise InvalidNoticeError("block without any OBLIGATION line")

    orphans = set(installments) - set(descriptions)
    if orphans:
        raise InvalidNoticeError(
            f"INSTALLMENT references a nonexistent obligation: {sorted(orphans)}"
        )
    orphans = set(payments) - set(descriptions)
    if orphans:
        raise InvalidNoticeError(f"PAYMENT references a nonexistent obligation: {sorted(orphans)}")

    obligations: list[PaymentObligation] = []
    for num in sorted(descriptions):
        fields = descriptions[num]
        if num not in payments:
            raise InvalidNoticeError(f"obligation {num} without a PAYMENT line")
        if not installments.get(num):
            raise InvalidNoticeError(f"obligation {num} without any INSTALLMENT")
        method, details = payments[num]
        obligations.append(
            PaymentObligation(
                description=fields["description"],
                beneficiary_name=fields["beneficiary"],
                honorific=_normalise_honorific(fields.get("honorific", "Sr.")),
                payment_method=method,
                payment_details=details,
                installments=installments[num],
            )
        )
    return obligations


def _parse_client_emails(value: str, doubts: list[str], internal_domain: str) -> list[str]:
    """The client's e-mail list; internal and invalid ones become doubts."""
    emails: list[str] = []
    suffix = f"@{internal_domain}"
    for raw in value.replace(",", ";").split(";"):
        e = raw.strip()
        if not e:
            continue
        if not _EMAIL_RE.match(e):
            doubts.append(f"client e-mail with an invalid format: {e!r}")
            continue
        if e.lower().endswith(suffix):
            doubts.append(f"internal e-mail ({e}) given as the client's e-mail: removed")
            continue
        if e.lower() not in {x.lower() for x in emails}:
            emails.append(e)
    return emails


def parse_notice(content: str, *, internal_domain: str = DEFAULT_INTERNAL_DOMAIN) -> ParsedNotice:
    """Reads a notice (HTML or text) and returns the structured agreement."""
    text = html_to_text(content)
    m = _BLOCK_RE.search(text)
    if not m:
        raise InvalidNoticeError("AUTOMATION-DATA block not found")
    version = int(m.group("version"))
    if version != SUPPORTED_VERSION:
        raise InvalidNoticeError(
            f"block version {version} not supported (expected v{SUPPORTED_VERSION})"
        )

    unique, repeatable = _collect_lines(m.group("body"))

    for required in ("OPERATION", "CASE", "CLIENT", "COUNTERPARTY", "LATE_CLAUSE"):
        if not unique.get(required):
            raise InvalidNoticeError(f"required key missing: {required}")

    operation = _first_token(unique["OPERATION"]).upper()
    if operation not in ("REGISTER", "CORRECTION"):
        raise InvalidNoticeError(f"invalid OPERATION: {unique['OPERATION']!r}")

    case_number = unique["CASE"].strip()
    if not CASE_NUMBER_RE.match(case_number):
        raise InvalidNoticeError(f"CASE outside the CNJ pattern: {case_number!r}")

    total: Decimal | None = None
    if unique.get("TOTAL_AMOUNT"):
        total = _parse_amount(_first_token(unique["TOTAL_AMOUNT"]), "TOTAL_AMOUNT")

    doubts: list[str] = []
    client_emails = _parse_client_emails(unique.get("CLIENT_EMAILS", ""), doubts, internal_domain)
    if not client_emails:
        doubts.append("no valid client e-mail given")

    lawyer = unique.get("LAWYER", "").strip()
    if not lawyer:
        doubts.append("notice without a responsible LAWYER")
    elif not lawyer.lower().endswith(f"@{internal_domain}"):
        doubts.append(f"LAWYER outside the firm's domain: {lawyer}")

    # extra copies: internal addresses ARE allowed here (unlike CLIENT_EMAILS); an invalid
    # format becomes a doubt
    cc_extra: list[str] = []
    for raw in unique.get("CC_EXTRA", "").replace(",", ";").split(";"):
        e = raw.strip()
        if not e:
            continue
        if not _EMAIL_RE.match(e):
            doubts.append(f"CC_EXTRA with an invalid format: {e!r}")
            continue
        if e.lower() not in {x.lower() for x in cc_extra}:
            cc_extra.append(e)

    obligations = _parse_obligations(repeatable)

    # soft checks: schedule in order and the sum versus the declared total
    for idx, obligation in enumerate(obligations, start=1):
        dues = [i.due for i in obligation.installments]
        if dues != sorted(dues):
            doubts.append(f"obligation {idx}: installments out of chronological order")

    if total is not None:
        total_installments = sum(i.amount for o in obligations for i in o.installments)
        count = sum(len(o.installments) for o in obligations)
        # the tolerance absorbs the cent rounding of dividing the total
        tolerance = Decimal("0.01") * count
        if repeatable["NO_COLLECTION"]:
            # part of the settlement is paid outside the installments (court order, deposit
            # withdrawal): the sum of the installments MUST stay below the declared total,
            # but equality cannot be checked without interpreting free text
            if total_installments > total + tolerance:
                doubts.append(
                    f"sum of the installments (R$ {total_installments}) exceeds TOTAL_AMOUNT (R$ {total})"
                )
        elif abs(total_installments - total) > tolerance:
            doubts.append(
                f"sum of the installments (R$ {total_installments}) differs from TOTAL_AMOUNT (R$ {total})"
            )

    agreement = ExtractedAgreement(
        case_number=case_number,
        claimant=unique["COUNTERPARTY"].strip(),
        payer=unique["CLIENT"].strip(),
        late_clause=unique["LATE_CLAUSE"].strip(),
        obligations=obligations,
        total_amount=total,
        no_collection=repeatable["NO_COLLECTION"],
        confidence="high" if not doubts else "medium",
        doubts=doubts,
    )
    return ParsedNotice(
        operation=operation,
        agreement=agreement,
        client_emails=client_emails,
        lawyer=lawyer,
        client_side=_first_token(unique.get("CLIENT_SIDE", "")).upper(),
        court=unique.get("COURT", "").strip(),
        duties=repeatable["DUTY"],
        cc_extra=cc_extra,
    )
