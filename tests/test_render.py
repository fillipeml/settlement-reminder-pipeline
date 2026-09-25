from settlement_reminders.mailer import render_reminder, wrap_branded
from tests.factories import CASE_B, make_agreement


def test_subject_follows_the_standard():
    agreement = make_agreement()
    second = agreement.installments()[1]
    subject, _ = render_reminder(second)
    assert subject == (
        f"LEMBRETE DE PAGAMENTO DA 2ª PARCELA DE ACORDO - {CASE_B} - SICRANA DE TAL X EMPRESA RECLAMADA LTDA"
    )


def test_body_has_the_key_elements():
    agreement = make_agreement()
    second = agreement.installments()[1]
    _, html = render_reminder(second)
    assert "Prezados, bom dia!" in html
    assert "segunda parcela" in html
    assert "15/07/2026 (quarta-feira)" in html
    assert "R$ 2.166,66 (dois mil cento e sessenta e seis reais e sessenta e seis centavos)" in html
    assert "à Sra. SICRANA DE TAL" in html
    assert "Permanecemos à disposição." in html
    # bank details with a real line break (not escaped)
    assert "<br />" in html
    assert "&lt;br" not in html


def test_last_installment_and_postponement():
    agreement = make_agreement()
    third = agreement.installments()[2]  # 15/08 Saturday -> 17/08
    _, html = render_reminder(third)
    assert "terceira e última parcela" in html
    assert "17/08/2026 (segunda-feira)" in html
    assert "prorrogado para o primeiro dia útil" in html
    assert "15/08/2026 (sábado)" in html


def test_branded_frame_carries_mark_signature_and_footer():
    html, images = wrap_branded(
        "<p>corpo</p>", signature="Controladoria Jurídica", firm_name="Example Law Firm"
    )
    assert len(images) == 1 and images[0]["contentId"] == "brand-mark"
    assert "cid:brand-mark" in html
    assert "Example Law Firm" in html
    assert "Atenciosamente" in html and "Controladoria Jurídica" in html
    assert "Conteúdo confidencial" in html
    # a body that carries its own signature gets no extra one
    no_signature, _ = wrap_branded("<p>corpo</p>", signature=None)
    assert "Atenciosamente" not in no_signature
