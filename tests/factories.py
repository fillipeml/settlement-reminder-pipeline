"""Test factories: fictional agreements, extracted agreements and notices.

Every case number is dated 2099 with invalid check digits; every e-mail domain is reserved.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from settlement_reminders.ingest.schema import (
    ExtractedAgreement,
    ExtractedInstallment,
    PaymentObligation,
)
from settlement_reminders.models import Agreement

CASE_B = "0001979-51.2099.5.99.0002"  # the structured-source default
CASE_NOTICE = "0001234-74.2099.5.99.0001"  # the notice default
CASE_EXTRACTED = "0001111-27.2099.8.99.0001"  # the extracted default
CASE_OTHER = "0000002-09.2099.5.99.0007"
CASE_PAYER_RULE = "0000165-98.2099.5.99.0003"
CASE_OUTSIDER = "0009999-19.2099.5.99.0006"
CASE_MISSING = "0000000-00.0000.0.00.0000"


def make_agreement(**over) -> Agreement:
    """An `Agreement` with defaults; override fields with kwargs."""
    data = dict(
        case_number=CASE_B,
        claimant="Sicrana de Tal",
        payer="Empresa Reclamada LTDA",
        beneficiary_name="Sicrana de Tal",
        honorific="Sra.",
        client_emails="client@example.com",
        lawyer_email="lawyer@lawfirm.example",
        installment_amount=Decimal("2166.66"),
        installment_count=3,
        first_due=date(2026, 6, 15),
        payment_method="depósito ou transferência",
        payment_details="Banco Exemplo (000)\nCorrentista: Maria;\nPIX: 000.",
        late_clause="em caso de atraso, haverá multa de 50% a partir do 5º dia de mora",
        active=True,
    )
    data.update(over)
    return Agreement(**data)


def make_extracted_agreement(**over) -> ExtractedAgreement:
    """Fictional, but mirroring the structure of real settlements: several obligations
    (the credit + a final installment to the attorney), different amounts and a lump-sum
    component that generates no reminder."""
    data = dict(
        case_number=CASE_EXTRACTED,
        claimant="Fulano Comprador da Silva",
        payer="Construtora Exemplo LTDA",
        late_clause=(
            "em caso de atraso no pagamento da parcela, havera incidencia de "
            "clausula penal de 20% (vinte por cento) sobre as parcelas inadimplidas"
        ),
        obligations=[
            PaymentObligation(
                description="main credit",
                beneficiary_name="Fulano Comprador da Silva",
                honorific="Sr.",
                payment_method="transferencia bancaria",
                payment_details=(
                    "Titular: Fulano Comprador da Silva;\nCPF: 000.000.000-00;\n"
                    "Banco: 000 - Banco Exemplo;\nAgencia: 0001;\nConta: 12345-6."
                ),
                installments=[
                    ExtractedInstallment(
                        number=i, due=date(2026, 5 + i, 14), amount=Decimal("3200.00")
                    )
                    for i in range(1, 7)
                ],
            ),
            PaymentObligation(
                description="final installment to the attorney",
                beneficiary_name="Beltrano Procurador Souza",
                honorific="Sr.",
                payment_method="transferencia bancaria",
                payment_details=(
                    "Titular: Beltrano Procurador Souza;\nCPF: 000.000.000-00;\n"
                    "Banco: 000 - Banco Exemplo;\nAgencia: 0002;\nConta: 98765-4;\n"
                    "Chave PIX (telefone): (00) 90000-0000."
                ),
                installments=[
                    ExtractedInstallment(
                        number=7, due=date(2026, 12, 14), amount=Decimal("4800.00")
                    )
                ],
            ),
        ],
        total_amount=Decimal("24000.00"),
        no_collection=["R$ 8.058,24 paid at once through a court order (appeal deposit)"],
        confidence="high",
        doubts=[],
    )
    data.update(over)
    return ExtractedAgreement(**data)


def make_block(**replace: str | None) -> str:
    """A valid default block; lines can be replaced/removed by key."""
    lines = {
        "OPERATION": "OPERATION: REGISTER",
        "CASE": f"CASE: {CASE_NOTICE}",
        "CLIENT": "CLIENT: EXEMPLO ENGENHARIA LTDA",
        "CLIENT_SIDE": "CLIENT_SIDE: DEFENDANT",
        "COUNTERPARTY": "COUNTERPARTY: FULANO DE TAL",
        "CLIENT_EMAILS": "CLIENT_EMAILS: finance@exemplo.example; board@exemplo.example",
        "LAWYER": "LAWYER: lawyer@lawfirm.example",
        "COURT": "COURT: 1ª Vara do Trabalho | Cidade Exemplo - UF",
        "TOTAL_AMOUNT": "TOTAL_AMOUNT: 9000.00",
        "LATE_CLAUSE": "LATE_CLAUSE: em caso de atraso, multa de 50% (cinquenta por cento)",
        "OBLIGATION": "OBLIGATION: 1 | crédito do reclamante | beneficiary=Fulano de Tal | honorific=Sr.",
        "PAYMENT": "PAYMENT: 1 | depósito ou transferência | Banco 000; Ag 0001; CC 12345-6; PIX 000.000.000-00",
        "INSTALLMENTS": (
            "INSTALLMENT: 1 | 1 | 2026-09-15 | 3000.00\n"
            "INSTALLMENT: 1 | 2 | 2026-10-15 | 3000.00\n"
            "INSTALLMENT: 1 | 3 | 2026-11-16 | 3000.00"
        ),
    }
    for key, value in replace.items():
        if value is None:
            lines.pop(key)
        else:
            lines[key] = value
    body = "\n".join(v for v in lines.values() if v)
    return f"=== AUTOMATION-DATA v1 ===\n{body}\n=== END AUTOMATION-DATA ==="


def make_notice(**replace: str | None) -> str:
    return "Prezados, bom dia!\n\nServimo-nos do presente para informar...\n\n" + make_block(
        **replace
    )
