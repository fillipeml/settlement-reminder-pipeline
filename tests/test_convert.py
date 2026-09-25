"""Conversion ExtractedAgreement -> Installments (and backward compatibility)."""

from __future__ import annotations

from decimal import Decimal

from settlement_reminders.ingest import installments_from_extracted
from settlement_reminders.mailer import render_reminder
from tests.factories import CASE_B, CASE_EXTRACTED, make_agreement, make_extracted_agreement


def test_converts_obligations_into_ordered_installments():
    installments = installments_from_extracted(make_extracted_agreement(), ["finance@example.com"])

    assert len(installments) == 7
    assert [i.original_due for i in installments] == sorted(i.original_due for i in installments)

    first = installments[0]
    assert first.number == 1
    assert first.amount == Decimal("3200.00")
    assert first.obligation_id == 1
    assert first.beneficiary_name == "Fulano Comprador da Silva"
    assert [str(e) for e in first.agreement.client_emails] == ["finance@example.com"]

    last = installments[-1]
    assert last.number == 7
    assert last.amount == Decimal("4800.00")
    assert last.obligation_id == 2
    assert last.beneficiary_name == "Beltrano Procurador Souza"
    assert last.is_last is True
    assert not any(i.is_last for i in installments[:-1])


def test_external_id_distinguishes_obligations_with_the_same_numbering():
    # a labour-court pattern: the credit and the fees both have an installment no. 1
    extracted = make_extracted_agreement()
    extracted.obligations[1].installments[0].number = 1

    installments = installments_from_extracted(extracted, [])
    ids = [i.external_id for i in installments]

    assert len(ids) == len(set(ids)), "the external id must be unique across obligations"
    assert any(i.endswith("#O1P1") for i in ids)
    assert any(i.endswith("#O2P1") for i in ids)


def test_legacy_external_id_stays_compatible_with_the_history():
    # structured sources keep generating the old format (idempotency preserved)
    installment = make_agreement().installments()[0]
    assert installment.external_id == f"{CASE_B}#P1"


def test_render_uses_the_installments_own_data():
    installments = installments_from_extracted(make_extracted_agreement(), ["x@example.com"])
    subject, html = render_reminder(installments[-1])

    assert "LEMBRETE DE PAGAMENTO DA 7ª PARCELA DE ACORDO" in subject
    assert CASE_EXTRACTED in subject
    assert "sétima e última parcela" in html
    assert "R$ 4.800,00 (quatro mil e oitocentos reais)" in html
    assert "ao Sr. BELTRANO PROCURADOR SOUZA" in html
    assert "clausula penal de 20%" in html
