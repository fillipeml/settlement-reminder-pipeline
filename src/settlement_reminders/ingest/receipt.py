"""Reading payment receipts (PDF or image) with the model.

The client's reply (or the lawyer's forward) brings the receipt, a PDF or a screenshot of a
PIX transfer, and the automation settles THE MATCHING INSTALLMENT. The model only READS the
document; the settlement is a deterministic match by amount (`match_installment`): without
an unambiguous match nothing is settled and the case goes to a human.
"""

from __future__ import annotations

import base64
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from settlement_reminders.ingest.extract import DEFAULT_MODEL, usage_info
from settlement_reminders.models import Installment

logger = logging.getLogger(__name__)

_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Binary signature -> real media type. The attachment's extension LIES: an 'image.png' that
# was a JPEG made the API refuse the whole message (400) and two real receipts in the same
# e-mail went unread; the client was collected the day after paying.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF", "application/pdf"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def media_type(content: bytes, name: str) -> str | None:
    """The attachment's real type: by the bytes; the extension only when unrecognised."""
    for prefix, media in _SIGNATURES:
        if content.startswith(prefix):
            return media
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return _MEDIA_TYPES.get(Path(name or "").suffix.lower())


_SYSTEM = """\
You read payment receipts (bank transfer, deposit, PIX) sent by clients of a Brazilian law
firm, in order to settle installments of court settlements. The documents are in Portuguese.

Rules:
1. Extract ONLY what is visible in the document. NEVER infer an amount, a date or a
   beneficiary: an illegible/absent field stays null (or empty).
2. `is_receipt` = true only when the document proves a COMPLETED payment (transfer,
   deposit, PIX). A bank slip, an invoice, a scheduling, a settlement agreement or any other
   document = false.
3. A SCHEDULING receipt (a future payment, not yet completed): `is_receipt` = true and
   `scheduled` = true.
4. Amounts in BRL with a decimal point and two places (e.g. 1794.17); dates as YYYY-MM-DD.
"""


class ExtractedReceipt(BaseModel):
    """The result of reading a receipt."""

    model_config = ConfigDict(extra="forbid")

    is_receipt: bool = Field(
        description="true only when the document proves a completed payment (transfer, deposit or PIX). "
        "A bank slip, an invoice or any other document: false."
    )
    scheduled: bool = Field(
        default=False, description="true when it is a SCHEDULING receipt (a future payment)."
    )
    amount: Decimal | None = Field(
        default=None,
        description="Amount paid in BRL, decimal point (e.g. '1794.17'); null if illegible.",
    )
    paid_on: date | None = Field(
        default=None, description="Payment date (YYYY-MM-DD); null if illegible."
    )
    beneficiary: str = Field(default="", description="Name of the payee/beneficiary, as written.")
    institution: str = Field(default="", description="Destination bank/institution, if shown.")
    transaction_id: str = Field(default="", description="Transaction ID/authentication, if shown.")
    notes: list[str] = Field(
        default_factory=list, description="Any ambiguous or atypical point of the document."
    )


class ReceiptError(RuntimeError):
    """Failed to read a receipt (transient: the sweep tries again)."""


class ReceiptReader:
    """Reads a receipt (PDF/JPG/PNG) and returns an `ExtractedReceipt`."""

    def __init__(self, client=None, *, api_key: str = "", model: str = DEFAULT_MODEL) -> None:
        if client is None:
            if not api_key:
                raise ReceiptError(
                    "ANTHROPIC_API_KEY missing: set it to read receipts (or inject a built client)."
                )
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
        self.client = client
        self.model = model

    def read(self, content: bytes, *, name: str = "") -> ExtractedReceipt:
        """Reads the receipt; the type (PDF/image) comes from the file's BYTES."""
        media = media_type(content, name)
        if media is None:
            raise ReceiptError(f"unsupported attachment type: {name!r}")

        data = base64.standard_b64encode(content).decode("ascii")
        block = (
            {"type": "document", "source": {"type": "base64", "media_type": media, "data": data}}
            if media == "application/pdf"
            else {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}
        )
        label = f" ({name})" if name else ""
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            system=_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": [
                        block,
                        {
                            "type": "text",
                            "text": f"Read the possible receipt attached{label}, following the schema and the system rules.",
                        },
                    ],
                }
            ],
            output_format=ExtractedReceipt,
        )
        receipt = response.parsed_output
        if receipt is None:
            raise ReceiptError(
                f"Reading returned no data{label} (stop_reason={getattr(response, 'stop_reason', '?')})."
            )
        logger.info(
            "Receipt read%s: is_receipt=%s amount=%s paid_on=%s%s",
            label,
            receipt.is_receipt,
            receipt.amount,
            receipt.paid_on,
            usage_info(response),
        )
        return receipt


def match_installment(
    receipt: ExtractedReceipt, installments: list[Installment], settled: set[str]
) -> Installment | None:
    """The open installment that matches the receipt, or None.

    Deterministic and conservative match: the exact amount (one-cent tolerance). Completed
    payments only (a scheduling does not settle). Among installments of the same amount,
    settles the one with the oldest due date still open: the next equal receipt settles
    the following one.
    """
    if not receipt.is_receipt or receipt.scheduled or receipt.amount is None:
        return None
    open_ones = [i for i in installments if i.external_id not in settled]
    candidates = [i for i in open_ones if abs(i.amount - receipt.amount) <= Decimal("0.01")]
    if not candidates:
        return None
    return min(candidates, key=lambda i: (i.original_due, i.obligation_id, i.number))
