"""Normalised domain models, independent of the source they came from.

There are two entry paths:

- Structured sources (Excel, a SharePoint list) produce `Agreement` (a single schedule of
  equal monthly installments), expanded into `Installment`s.
- Notices and drafts produce `ExtractedAgreement` (see `ingest.schema`), converted straight
  into `Installment`s (`ingest.convert`), because real settlements have several obligations,
  variable amounts and a beneficiary/account that changes per installment.

That is why the payment data (amount, beneficiary, account) live on the `Installment`;
`AgreementBase` holds what is common to the whole agreement (case, parties, e-mails, the
late-payment clause).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from pydantic import BaseModel, EmailStr, Field, field_validator


class AgreementBase(BaseModel):
    """Data common to a settlement, independent of the schedule."""

    case_number: str
    claimant: str  # who receives the settlement
    payer: str  # the firm's client, who pays

    client_emails: list[EmailStr] = Field(default_factory=list)
    lawyer_email: EmailStr | None = None
    # extra copies of reminders/collections, indicated by the lawyer in the notice (besides
    # the controllership, which is always in Cc)
    cc_emails: list[EmailStr] = Field(default_factory=list)
    # conversation (Graph conversationId) opened by the agreement's notice: reminders and
    # collections reply in it. "" = no known thread.
    thread_id: str = ""

    # late-payment clause: the text after "Esclarecemos que, " (no final period)
    late_clause: str = ""

    active: bool = True

    @field_validator("case_number", "claimant", "payer", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

    @field_validator("client_emails", "cc_emails", mode="before")
    @classmethod
    def _split_emails(cls, v: object) -> object:
        """Accepts 'a@x.com, b@y.com' (a string) as a list."""
        if isinstance(v, str):
            return [e.strip() for e in v.replace(";", ",").split(",") if e.strip()]
        return v


class Agreement(AgreementBase):
    """An agreement from a structured source: equal monthly installments."""

    beneficiary_name: str
    honorific: str = "Sr."  # Sr. | Sra. | Dr. | Dra. (as used in the Portuguese e-mail)

    installment_amount: Decimal = Field(gt=0)
    installment_count: int = Field(ge=1)
    first_due: date

    # faithful text (comes from the settlement, varies per agreement):
    # the phrase after "mediante", e.g. "depósito ou transferência" / "transferência bancária"
    payment_method: str = "depósito ou transferência"
    # bank details block, exactly as it must appear (multi-line)
    payment_details: str = ""

    @field_validator("beneficiary_name", mode="before")
    @classmethod
    def _strip_beneficiary(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

    def installments(self) -> list[Installment]:
        """Expands the agreement into its schedule."""
        out: list[Installment] = []
        for i in range(1, self.installment_count + 1):
            due = self.first_due + relativedelta(months=i - 1)
            out.append(
                Installment(
                    agreement=self,
                    number=i,
                    total=self.installment_count,
                    original_due=due,
                    amount=self.installment_amount,
                    beneficiary_name=self.beneficiary_name,
                    honorific=self.honorific,
                    payment_method=self.payment_method,
                    payment_details=self.payment_details,
                    is_last=(i == self.installment_count),
                )
            )
        return out


class Installment(BaseModel):
    """One installment of an agreement, with its own payment data.

    `number` follows the numbering of the settlement/source (it is what the e-mail shows);
    `is_last` marks the installment with the last due date of the WHOLE agreement (not of
    the obligation), because the e-mail says "Nth and last installment".
    """

    agreement: AgreementBase
    number: int
    total: int
    original_due: date

    amount: Decimal
    beneficiary_name: str
    honorific: str = "Sr."
    payment_method: str = "depósito ou transferência"
    payment_details: str = ""

    is_last: bool = False
    # 0 = source with a single schedule (Excel/SharePoint); >= 1 = index of the obligation
    # in the notice (credit, fees...). Part of the external id.
    obligation_id: int = 0

    @property
    def external_id(self) -> str:
        """Stable key for idempotency (compatible with the legacy history)."""
        if self.obligation_id:
            return f"{self.agreement.case_number}#O{self.obligation_id}P{self.number}"
        return f"{self.agreement.case_number}#P{self.number}"
