"""The parser of the skill's notices (the AUTOMATION-DATA block)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from settlement_reminders.ingest import (
    AgreementStore,
    InvalidNoticeError,
    parse_notice,
    process_notice,
)
from settlement_reminders.ingest.notice import has_data_block, html_to_text, strip_data_block
from tests.factories import CASE_MISSING, CASE_NOTICE, CASE_OUTSIDER, make_block, make_notice

# ---------------------------------------------------------------- parser ----


def test_parse_complete_notice():
    parsed = parse_notice(make_notice())

    agreement = parsed.agreement
    assert parsed.operation == "REGISTER"
    assert agreement.case_number == CASE_NOTICE
    assert agreement.payer == "EXEMPLO ENGENHARIA LTDA"  # the client = who pays
    assert agreement.claimant == "FULANO DE TAL"  # who receives
    assert parsed.client_emails == ["finance@exemplo.example", "board@exemplo.example"]
    assert parsed.lawyer == "lawyer@lawfirm.example"
    assert parsed.client_side == "DEFENDANT"
    assert agreement.confidence == "high"
    assert agreement.doubts == []
    assert agreement.total_amount == Decimal("9000.00")

    (obligation,) = agreement.obligations
    assert obligation.beneficiary_name == "Fulano de Tal"
    assert obligation.honorific == "Sr."
    assert [i.number for i in obligation.installments] == [1, 2, 3]
    assert obligation.installments[0].due == date(2026, 9, 15)
    assert obligation.installments[0].amount == Decimal("3000.00")


def test_parse_two_obligations_and_auxiliary_records():
    text = make_notice(
        TOTAL_AMOUNT=None,
        OBLIGATION=(
            "OBLIGATION: 1 | crédito | beneficiary=Fulano | honorific=Sr.\n"
            "OBLIGATION: 2 | honorários | beneficiary=Escritório X | honorific=Sr.\n"
            "NO_COLLECTION: court order of R$ 14.705,67\n"
            "DUTY: CTPS update by 2026-09-15"
        ),
        PAYMENT="PAYMENT: 1 | PIX | Key 1\nPAYMENT: 2 | depósito | Bank 2; Account 2",
        INSTALLMENTS="INSTALLMENT: 1 | 1 | 2026-09-15 | 100.00\nINSTALLMENT: 2 | 1 | 2026-09-15 | 50.00",
    )
    parsed = parse_notice(text)

    assert len(parsed.agreement.obligations) == 2
    assert parsed.agreement.no_collection == ["court order of R$ 14.705,67"]
    assert parsed.duties == ["CTPS update by 2026-09-15"]
    assert parsed.agreement.confidence == "high"


def test_parse_accepts_html_body_with_entities():
    html = (
        "<p>Prezados, boa tarde!</p><pre>"
        + make_block(CLIENT="CLIENT: SILVA &amp; FILHOS LTDA").replace("\n", "<br>")
        + "</pre>"
    )
    parsed = parse_notice(html)
    assert parsed.agreement.payer == "SILVA & FILHOS LTDA"


def test_has_data_block():
    assert has_data_block(make_notice())
    assert not has_data_block("Prezados, segue anexa a minuta do acordo.")


def test_html_to_text_keeps_plain_text():
    assert html_to_text("line 1\nline 2") == "line 1\nline 2"


def test_strip_data_block_cleans_the_client_version():
    html = (
        "<p>Prezados, bom dia!</p><p>1ª parcela...</p>"
        "<p>— automation data, internal use —</p>"
        "<pre>" + make_block() + "</pre>"
    )
    clean = strip_data_block(html)
    assert "AUTOMATION-DATA" not in clean
    assert "automation data" not in clean
    assert "<pre>" not in clean
    assert "Prezados, bom dia!" in clean
    assert "1ª parcela" in clean

    # the plain-text variant
    clean_txt = strip_data_block("human body\n\n" + make_block())
    assert "AUTOMATION-DATA" not in clean_txt
    assert clean_txt == "human body"


def test_rewrapped_line_is_treated_as_a_continuation():
    text = make_notice(
        LATE_CLAUSE="LATE_CLAUSE: em caso de atraso, multa de 50%\naplicável a partir do 5º dia de mora"
    )
    parsed = parse_notice(text)
    assert (
        parsed.agreement.late_clause
        == "em caso de atraso, multa de 50% aplicável a partir do 5º dia de mora"
    )
    assert parsed.agreement.confidence == "high"


def test_correction_operation():
    parsed = parse_notice(make_notice(OPERATION="OPERATION: CORRECTION"))
    assert parsed.operation == "CORRECTION"


# --------------------------------------------------- structural errors ----


def test_missing_block_is_an_error():
    with pytest.raises(InvalidNoticeError, match="not found"):
        parse_notice("any e-mail without a block")


def test_unknown_version_is_an_error():
    text = make_notice().replace("AUTOMATION-DATA v1", "AUTOMATION-DATA v2")
    with pytest.raises(InvalidNoticeError, match="version 2"):
        parse_notice(text)


@pytest.mark.parametrize("key", ["OPERATION", "CASE", "CLIENT", "LATE_CLAUSE"])
def test_missing_required_key_is_an_error(key):
    with pytest.raises(InvalidNoticeError, match=key):
        parse_notice(make_notice(**{key: None}))


def test_case_outside_the_cnj_pattern_is_an_error():
    with pytest.raises(InvalidNoticeError, match="CNJ"):
        parse_notice(make_notice(CASE="CASE: 12345/2026"))


def test_installment_of_a_nonexistent_obligation_is_an_error():
    with pytest.raises(InvalidNoticeError, match="nonexistent"):
        parse_notice(make_notice(INSTALLMENTS="INSTALLMENT: 9 | 1 | 2026-09-15 | 3000.00"))


def test_obligation_without_payment_is_an_error():
    with pytest.raises(InvalidNoticeError, match="without a PAYMENT line"):
        parse_notice(make_notice(PAYMENT=None))


def test_obligation_without_installments_is_an_error():
    with pytest.raises(InvalidNoticeError, match="without any INSTALLMENT"):
        parse_notice(make_notice(INSTALLMENTS=None))


def test_invalid_honorific_is_an_error():
    with pytest.raises(InvalidNoticeError, match="honorific"):
        parse_notice(
            make_notice(OBLIGATION="OBLIGATION: 1 | crédito | beneficiary=Fulano | honorific=Exmo.")
        )


@pytest.mark.parametrize(
    "given,expected", [("Sr.", "Sr."), ("sra", "Sra."), ("Dr.", "Dr."), ("dra", "Dra.")]
)
def test_accepted_honorifics(given, expected):
    # a lawyer/attorney beneficiary needs Dr./Dra. (a real case: installments paid to the
    # counterparty's attorney)
    text = make_notice(
        OBLIGATION=f"OBLIGATION: 1 | crédito | beneficiary=Nara Gomes | honorific={given}"
    )
    (obligation,) = parse_notice(text).agreement.obligations
    assert obligation.honorific == expected


def test_non_positive_amount_is_an_error():
    with pytest.raises(InvalidNoticeError, match="non-positive"):
        parse_notice(make_notice(INSTALLMENTS="INSTALLMENT: 1 | 1 | 2026-09-15 | 0.00"))


def test_duplicated_unique_key_is_an_error():
    text = make_notice(CASE=f"CASE: {CASE_NOTICE}\nCASE: {CASE_OUTSIDER}")
    with pytest.raises(InvalidNoticeError, match="duplicated"):
        parse_notice(text)


# -------------------------------------------------------- soft checks ----


def test_divergent_sum_becomes_a_doubt():
    parsed = parse_notice(make_notice(TOTAL_AMOUNT="TOTAL_AMOUNT: 9500.00"))
    assert parsed.agreement.confidence == "medium"
    assert any("differs from TOTAL_AMOUNT" in d for d in parsed.agreement.doubts)


def test_cent_rounding_is_not_a_doubt():
    text = make_notice(
        TOTAL_AMOUNT="TOTAL_AMOUNT: 6500.00",
        INSTALLMENTS=(
            "INSTALLMENT: 1 | 1 | 2026-06-15 | 2166.66\n"
            "INSTALLMENT: 1 | 2 | 2026-07-15 | 2166.66\n"
            "INSTALLMENT: 1 | 3 | 2026-08-17 | 2166.66"
        ),
    )
    assert parse_notice(text).agreement.confidence == "high"


def test_larger_total_with_a_court_order_is_not_a_doubt():
    # a real pattern: TOTAL_AMOUNT includes the part paid through a court order, which does
    # not become an installment: the sum STAYS below the total
    text = make_notice(
        TOTAL_AMOUNT="TOTAL_AMOUNT: 29400.00",
        OBLIGATION="OBLIGATION: 1 | crédito | beneficiary=Nara Gomes | honorific=Dra.\n"
        "NO_COLLECTION: R$ 8.058,24 paid through a court order",
        INSTALLMENTS="INSTALLMENT: 1 | 1 | 2026-06-05 | 10000.00\nINSTALLMENT: 1 | 2 | 2026-07-05 | 11341.70",
    )
    parsed = parse_notice(text)
    assert parsed.agreement.confidence == "high"
    assert parsed.agreement.doubts == []


def test_installments_above_the_total_is_a_doubt_even_with_a_court_order():
    text = make_notice(
        TOTAL_AMOUNT="TOTAL_AMOUNT: 1000.00",
        OBLIGATION="OBLIGATION: 1 | crédito | beneficiary=Fulano | honorific=Sr.\nNO_COLLECTION: R$ 500,00 through a court order",
        INSTALLMENTS="INSTALLMENT: 1 | 1 | 2026-06-05 | 5000.00",
    )
    parsed = parse_notice(text)
    assert any("exceeds TOTAL_AMOUNT" in d for d in parsed.agreement.doubts)


def test_brazilian_formats_are_tolerated():
    # the spec asks for ISO/decimal point, but a slip of the skill in Brazilian format must
    # not bring the notice down
    text = make_notice(
        TOTAL_AMOUNT="TOTAL_AMOUNT: 9.000,00",
        INSTALLMENTS=(
            "INSTALLMENT: 1 | 1 | 15/09/2026 | 3.000,00\n"
            "INSTALLMENT: 1 | 2 | 2026-10-15 | 3000.00\n"
            "INSTALLMENT: 1 | 3 | 16/11/2026 | 3000,00"
        ),
    )
    parsed = parse_notice(text)
    (obligation,) = parsed.agreement.obligations
    assert obligation.installments[0].due == date(2026, 9, 15)
    assert obligation.installments[0].amount == Decimal("3000.00")
    assert obligation.installments[2].amount == Decimal("3000.00")
    assert parsed.agreement.total_amount == Decimal("9000.00")
    assert parsed.agreement.confidence == "high"


def test_invalid_date_and_amount_are_still_errors():
    with pytest.raises(InvalidNoticeError, match="invalid date"):
        parse_notice(make_notice(INSTALLMENTS="INSTALLMENT: 1 | 1 | 15-09-2026 | 100.00"))
    with pytest.raises(InvalidNoticeError, match="invalid amount"):
        parse_notice(make_notice(INSTALLMENTS="INSTALLMENT: 1 | 1 | 2026-09-15 | one hundred"))


def test_weekend_due_date_is_accepted_without_a_doubt():
    # the skill transcribes the settlement's date; moving it to a business day is the
    # engine's job (rules.due_date), never the skill's
    text = make_notice(
        TOTAL_AMOUNT=None,
        INSTALLMENTS="INSTALLMENT: 1 | 1 | 2026-09-05 | 100.00\nINSTALLMENT: 1 | 2 | 2026-10-05 | 100.00",
    )  # a Saturday
    parsed = parse_notice(text)
    assert parsed.agreement.confidence == "high"
    assert parsed.agreement.obligations[0].installments[0].due == date(2026, 9, 5)


def test_installments_out_of_order_is_a_doubt():
    text = make_notice(
        TOTAL_AMOUNT=None,
        INSTALLMENTS="INSTALLMENT: 1 | 1 | 2026-10-15 | 3000.00\nINSTALLMENT: 1 | 2 | 2026-09-15 | 3000.00",
    )
    parsed = parse_notice(text)
    assert any("out of chronological order" in d for d in parsed.agreement.doubts)


def test_internal_email_is_removed_with_a_doubt():
    text = make_notice(
        CLIENT_EMAILS="CLIENT_EMAILS: someone@lawfirm.example; client@exemplo.example"
    )
    parsed = parse_notice(text)
    assert parsed.client_emails == ["client@exemplo.example"]
    assert any("internal" in d for d in parsed.agreement.doubts)


def test_internal_domain_is_configurable():
    text = make_notice(
        CLIENT_EMAILS="CLIENT_EMAILS: someone@lawfirm.example; client@other-firm.example"
    )
    parsed = parse_notice(text, internal_domain="other-firm.example")
    assert parsed.client_emails == ["someone@lawfirm.example"]


def test_no_valid_email_is_a_doubt():
    parsed = parse_notice(make_notice(CLIENT_EMAILS="CLIENT_EMAILS:"))
    assert any("no valid client e-mail" in d for d in parsed.agreement.doubts)


def test_missing_lawyer_is_a_doubt():
    parsed = parse_notice(make_notice(LAWYER=None))
    assert any("LAWYER" in d for d in parsed.agreement.doubts)


def test_cc_extra_is_optional_and_accepts_internal_addresses(tmp_path):
    # without CC_EXTRA: an empty list, no doubt
    assert parse_notice(make_notice()).cc_extra == []

    text = make_notice(
        LAWYER="LAWYER: lawyer@lawfirm.example\nCC_EXTRA: coordinator@lawfirm.example; manager@exemplo.example"
    )
    parsed = parse_notice(text)
    assert parsed.cc_extra == ["coordinator@lawfirm.example", "manager@exemplo.example"]
    assert parsed.agreement.confidence == "high"

    # and it reaches the installments through the store (Cc of reminders/collections)
    store = _store(tmp_path)
    process_notice(text, store=store)
    installments = store.installments_of(CASE_NOTICE)
    assert [str(e) for e in installments[0].agreement.cc_emails] == [
        "coordinator@lawfirm.example",
        "manager@exemplo.example",
    ]


# --------------------------------------------------- process_notice ----


def _store(tmp_path) -> AgreementStore:
    return AgreementStore(str(tmp_path / "data.sqlite"))


def test_process_notice_registers_active_with_lawyer(tmp_path):
    store = _store(tmp_path)
    result = process_notice(make_notice(), store=store, origin="notice <lawyer@lawfirm.example>")

    assert result.status == "active"
    assert result.match_status == "notice"

    installments = store.installments_for_reminders()
    assert len(installments) == 3
    assert str(installments[0].agreement.lawyer_email) == "lawyer@lawfirm.example"
    assert [str(e) for e in installments[0].agreement.client_emails] == [
        "finance@exemplo.example",
        "board@exemplo.example",
    ]


def test_process_notice_with_a_doubt_stays_pending(tmp_path):
    store = _store(tmp_path)
    result = process_notice(make_notice(CLIENT_EMAILS="CLIENT_EMAILS:"), store=store)

    assert result.status == "pending"
    assert result.issues
    assert store.installments_for_reminders() == []


def test_correction_updates_an_existing_agreement(tmp_path):
    store = _store(tmp_path)
    process_notice(make_notice(), store=store)

    corrected = make_notice(
        OPERATION="OPERATION: CORRECTION", CLIENT_EMAILS="CLIENT_EMAILS: new@exemplo.example"
    )
    result = process_notice(corrected, store=store)

    assert result.status == "active"
    record = store.find(CASE_NOTICE)
    assert record.client_emails == ["new@exemplo.example"]


def test_correction_does_not_reopen_a_closed_agreement(tmp_path):
    store = _store(tmp_path)
    process_notice(make_notice(), store=store)
    store.close(CASE_NOTICE, "the client paid everything")

    result = process_notice(make_notice(OPERATION="OPERATION: CORRECTION"), store=store)
    assert result.status == "closed"


def test_set_emails_activates_an_agreement_pending_on_the_recipient(tmp_path):
    store = _store(tmp_path)
    # pending for the lack of a valid client e-mail
    process_notice(make_notice(CLIENT_EMAILS="CLIENT_EMAILS:"), store=store)
    assert store.find(CASE_NOTICE).status == "pending"

    status = store.set_emails(
        CASE_NOTICE, ["pedro.lopes@lawfirm.example"], reason="the lawyer's acceptance test"
    )

    assert status == "active"
    record = store.find(CASE_NOTICE)
    assert record.client_emails == ["pedro.lopes@lawfirm.example"]
    assert record.match_status == "manual"
    assert len(store.installments_of(CASE_NOTICE)) == 3


def test_set_emails_does_not_touch_a_closed_agreement(tmp_path):
    store = _store(tmp_path)
    process_notice(make_notice(), store=store)
    store.close(CASE_NOTICE, "test")

    assert store.set_emails(CASE_NOTICE, ["x@y.com"]) is None
    assert store.set_emails(CASE_MISSING, ["x@y.com"]) is None


def test_migration_adds_the_missing_columns_to_an_old_database(tmp_path):
    import sqlite3

    db = tmp_path / "old.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE agreements (
                case_number TEXT PRIMARY KEY, data TEXT NOT NULL,
                client_emails TEXT NOT NULL DEFAULT '',
                match_status TEXT NOT NULL, match_info TEXT,
                status TEXT NOT NULL, issues TEXT NOT NULL DEFAULT '[]',
                origin TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                closed_at TEXT, closed_reason TEXT
            )
            """
        )

    store = AgreementStore(str(db))  # must migrate without an error
    process_notice(make_notice(), store=store)
    assert len(store.installments_for_reminders()) == 3
