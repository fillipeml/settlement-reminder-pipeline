"""Extraction schema of a settlement (the model's structured output and the notice parser's
target).

Designed from real settlements, which vary more than the `Agreement` model (equal monthly
installments) assumes:

- one settlement may have SEVERAL payment obligations (the main credit, attorney fees),
  each with its own schedule, beneficiary and bank account;
- installments may have different amounts (e.g. 6 x R$ 3,200 + 1 x R$ 4,800);
- the beneficiary may be the party or their lawyer/attorney-in-fact;
- lump-sum payments / court-released deposits do NOT generate reminders (they do not
  depend on an action of the paying client).

The field descriptions are read by the model during the extraction: they are part of the
prompt, not only documentation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Honorific = Literal["Sr.", "Sra.", "Dr.", "Dra."]
Confidence = Literal["high", "medium", "low"]


class ExtractedInstallment(BaseModel):
    """One installment of an obligation's schedule."""

    model_config = ConfigDict(extra="forbid")

    number: int = Field(
        description="Installment number as written in the settlement (e.g. 1, 2... 7)."
    )
    due: date = Field(description="Due date of the installment (YYYY-MM-DD).")
    amount: Decimal = Field(description="Amount in BRL, e.g. '3200.00' (decimal point, no 'R$').")


class PaymentObligation(BaseModel):
    """A payment block with its own schedule, beneficiary and account."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(
        description="Nature of the obligation, e.g. 'labour credit', 'attorney fees', 'final installment to the attorney'."
    )
    beneficiary_name: str = Field(
        description="Full name of the account holder who receives these installments."
    )
    honorific: Honorific = Field(
        description="Portuguese honorific of the beneficiary, used in the e-mail: 'Sr.'/'Sra.' for the party; "
        "'Dr.'/'Dra.' when the beneficiary is the lawyer or attorney-in-fact."
    )
    payment_method: str = Field(
        description="How the e-mail describes the payment method, faithful to the settlement, in Portuguese, e.g. "
        "'transferência bancária', 'depósito ou transferência'."
    )
    payment_details: str = Field(
        description="Bank details FAITHFUL to the settlement, one item per line, e.g.:\n"
        "Titular: FULANO;\nCPF: 000.000.000-00;\nBanco: 341 - Itaú;\nAgência: 3111;\nConta: 69892-8. "
        "Include the PIX key when there is one."
    )
    installments: list[ExtractedInstallment] = Field(
        description="The complete schedule of this obligation, in due-date order."
    )


class ExtractedAgreement(BaseModel):
    """The result of extracting a settlement draft."""

    model_config = ConfigDict(extra="forbid")

    case_number: str = Field(description="CNJ case number, e.g. '0001111-27.2099.8.99.0001'.")
    claimant: str = Field(
        description="Plaintiff/claimant/creditor party (who RECEIVES the settlement)."
    )
    payer: str = Field(description="Defendant/executed party (the firm's client, who PAYS).")
    late_clause: str = Field(
        description="The late-payment rule rewritten in Portuguese to complete the e-mail sentence 'Esclarecemos que, ___.': "
        "start in lower case, no final period. E.g. 'em caso de atraso no pagamento da parcela, haverá incidência de multa de "
        "50% (cinquenta por cento) sobre o valor inadimplido, aplicável a partir do 5º (quinto) dia de mora'."
    )
    obligations: list[PaymentObligation] = Field(
        description="Every obligation the paying client settles by deposit or transfer. Do NOT include lump-sum amounts, "
        "amounts released by court order or withdrawals of a court deposit."
    )
    total_amount: Decimal | None = Field(
        default=None, description="Total amount of the settlement declared in the draft, if any."
    )
    no_collection: list[str] = Field(
        default_factory=list,
        description="Components that do NOT generate reminders (lump sum, court order, deposit withdrawal), "
        "one line each, with the amount.",
    )
    confidence: Confidence = Field(
        description="'high' only when every essential datum (case, parties, schedules, accounts) is explicit and unambiguous."
    )
    doubts: list[str] = Field(
        default_factory=list,
        description="Every ambiguous, illegible or inferred point. NEVER guess a datum: record the doubt here. "
        "Empty only with high confidence.",
    )
