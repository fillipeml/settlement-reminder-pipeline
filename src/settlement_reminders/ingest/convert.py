"""Conversion of an `ExtractedAgreement` into `Installment`s ready for the engine.

Each obligation becomes a group of installments with its own beneficiary/account; `is_last`
marks the last due date of the WHOLE agreement (the e-mail says "Nth and last installment"
only on the installment that closes the agreement).
"""

from __future__ import annotations

from settlement_reminders.ingest.schema import ExtractedAgreement
from settlement_reminders.models import AgreementBase, Installment


def installments_from_extracted(
    extracted: ExtractedAgreement,
    client_emails: list[str],
    *,
    lawyer_email: str | None = None,
    cc_emails: list[str] | None = None,
    thread_id: str = "",
) -> list[Installment]:
    """Converts the extracted agreement into installments for reminders."""
    base = AgreementBase(
        case_number=extracted.case_number,
        claimant=extracted.claimant,
        payer=extracted.payer,
        client_emails=client_emails,
        lawyer_email=lawyer_email,
        cc_emails=cc_emails or [],
        thread_id=thread_id or "",
        late_clause=extracted.late_clause,
    )

    every = [
        (idx, obligation, installment)
        for idx, obligation in enumerate(extracted.obligations, start=1)
        for installment in obligation.installments
    ]
    if not every:
        return []

    total = len(every)
    last_due = max(i.due for _, _, i in every)

    installments = [
        Installment(
            agreement=base,
            number=i.number,
            total=total,
            original_due=i.due,
            amount=i.amount,
            beneficiary_name=obligation.beneficiary_name,
            honorific=obligation.honorific,
            payment_method=obligation.payment_method,
            payment_details=obligation.payment_details,
            is_last=(i.due == last_due),
            obligation_id=idx,
        )
        for idx, obligation, i in every
    ]
    installments.sort(key=lambda x: (x.original_due, x.obligation_id, x.number))
    return installments
