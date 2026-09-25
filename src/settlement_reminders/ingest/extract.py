"""Extraction of agreements from settlement drafts in PDF, through the model (legacy path).

Receives the PDF of the settlement and returns a validated `ExtractedAgreement`
(structured outputs -> Pydantic). The model is instructed to NEVER invent a missing datum:
an ambiguity becomes an item in `doubts` and lowers `confidence`; agreements without high
confidence do not enter the automatic cycle (they become an exception for human review).
"""

from __future__ import annotations

import base64
import logging
from datetime import date
from pathlib import Path

from settlement_reminders.ingest.notice import CASE_NUMBER_RE
from settlement_reminders.ingest.schema import ExtractedAgreement

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5"

_SYSTEM = """\
You extract data from settlement drafts/agreements of Brazilian court cases (written in
Portuguese) to feed automatic payment reminders sent to the paying client (the defendant /
executed party, the law firm's client).

Rules:
1. Extract ONLY what is written in the draft. NEVER invent or complete a missing datum:
   record the problem in `doubts` and adjust `confidence`.
2. Include in `obligations` only payments made by the paying client by deposit, transfer or
   PIX. Lump sums, amounts released by court order or withdrawals of a court deposit go in
   `no_collection`.
3. When the same credit changes account/beneficiary in the middle of the schedule (e.g. the
   last installment paid to the attorney), split it into distinct obligations, preserving
   the original numbering of the installments.
4. When the draft gives only the first date and says "the others on the same day of the
   following months", generate the complete schedule month by month.
5. `payment_details` must reproduce the bank details faithfully (holder, CPF/CNPJ, bank,
   branch, account, operation, PIX key), one item per line, in Portuguese.
6. Amounts in BRL with a decimal point and two places, no symbol (e.g. 3200.00).
7. Dates as YYYY-MM-DD. The Portuguese text fields (late clause, payment method, honorific)
   follow the descriptions of the schema.
"""


class ExtractionError(RuntimeError):
    """Failed to extract data from a draft."""


def usage_info(response: object) -> str:
    """Tokens of the call, for cost auditing in the logs ('' when unavailable)."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return ""
    return f" | tokens in={getattr(usage, 'input_tokens', '?')} out={getattr(usage, 'output_tokens', '?')}"


class AgreementExtractor:
    """Extracts an `ExtractedAgreement` from a PDF draft with the model."""

    def __init__(self, client=None, *, api_key: str = "", model: str = DEFAULT_MODEL) -> None:
        if client is None:
            if not api_key:
                raise ExtractionError(
                    "ANTHROPIC_API_KEY missing: set it to extract drafts (or inject a built client)."
                )
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
        self.client = client
        self.model = model

    def extract_pdf(self, pdf: bytes | str | Path, *, file_name: str = "") -> ExtractedAgreement:
        """Extracts the agreement from a PDF (bytes or a file path)."""
        if isinstance(pdf, str | Path):
            path = Path(pdf)
            file_name = file_name or path.name
            pdf = path.read_bytes()

        label = f" ({file_name})" if file_name else ""
        instruction = f"Extract the data of the attached settlement draft{label}, following the schema and the system rules."
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": base64.standard_b64encode(pdf).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": instruction},
                    ],
                }
            ],
            output_format=ExtractedAgreement,
        )
        agreement = response.parsed_output
        if agreement is None:
            raise ExtractionError(
                f"Extraction returned no data{label} (stop_reason={getattr(response, 'stop_reason', '?')})."
            )

        agreement.doubts.extend(check_consistency(agreement))
        if agreement.doubts and agreement.confidence == "high":
            agreement.confidence = "medium"
        logger.info(
            "Draft extracted%s: case=%s obligations=%d installments=%d confidence=%s doubts=%d%s",
            label,
            agreement.case_number,
            len(agreement.obligations),
            sum(len(o.installments) for o in agreement.obligations),
            agreement.confidence,
            len(agreement.doubts),
            usage_info(response),
        )
        return agreement


def check_consistency(agreement: ExtractedAgreement) -> list[str]:
    """Deterministic checks after the extraction; problems become `doubts`."""
    problems: list[str] = []

    if not CASE_NUMBER_RE.match(agreement.case_number.strip()):
        problems.append(f"case number outside the CNJ pattern: {agreement.case_number!r}")

    if not agreement.obligations:
        problems.append("no payment obligation extracted")

    for obligation in agreement.obligations:
        label = obligation.description or obligation.beneficiary_name
        if not obligation.installments:
            problems.append(f"obligation {label!r} without installments")
            continue
        if not obligation.payment_details.strip():
            problems.append(f"obligation {label!r} without bank details")

        previous: date | None = None
        seen: set[int] = set()
        for i in obligation.installments:
            if i.amount <= 0:
                problems.append(
                    f"installment {i.number} of {label!r} with an invalid amount: {i.amount}"
                )
            if i.number in seen:
                problems.append(f"repeated installment number in {label!r}: {i.number}")
            seen.add(i.number)
            if previous and i.due <= previous:
                problems.append(
                    f"due dates out of order in {label!r}: {i.due.isoformat()} after {previous.isoformat()}"
                )
            previous = i.due

    if agreement.total_amount is not None:
        total = sum(i.amount for o in agreement.obligations for i in o.installments)
        if total > agreement.total_amount:
            problems.append(
                f"sum of the installments (R$ {total}) exceeds the total amount of the settlement (R$ {agreement.total_amount})"
            )

    return problems
