"""Persistence of the registered agreements (AgreementStore)."""

from __future__ import annotations

from settlement_reminders.ingest import AgreementStore
from tests.factories import CASE_MISSING, CASE_OTHER, make_extracted_agreement


def _store(tmp_path) -> AgreementStore:
    return AgreementStore(str(tmp_path / "data.sqlite"))


def test_active_agreement_generates_installments_with_emails(tmp_path):
    store = _store(tmp_path)
    store.upsert(
        make_extracted_agreement(),
        client_emails=["finance@example.com", "legal@example.com"],
        match_status="ok",
        match_info="exact match",
        status="active",
        issues=[],
        origin="draft.pdf",
    )

    installments = store.installments_for_reminders()

    assert len(installments) == 7
    assert [str(e) for e in installments[0].agreement.client_emails] == [
        "finance@example.com",
        "legal@example.com",
    ]


def test_pending_agreement_generates_no_installments(tmp_path):
    store = _store(tmp_path)
    store.upsert(
        make_extracted_agreement(),
        client_emails=[],
        match_status="no_email",
        match_info="client without e-mail in the directory",
        status="pending",
        issues=["recipient (no_email): client without e-mail in the directory"],
    )

    assert store.installments_for_reminders() == []
    pending = store.list_agreements(status="pending")
    assert len(pending) == 1
    assert pending[0].issues != []


def test_upsert_updates_an_existing_agreement(tmp_path):
    store = _store(tmp_path)
    extracted = make_extracted_agreement()
    store.upsert(
        extracted,
        client_emails=[],
        match_status="no_email",
        match_info="no e-mail",
        status="pending",
        issues=["recipient (no_email): no e-mail"],
    )

    # the team completed the e-mail; the draft was reprocessed
    store.upsert(
        extracted,
        client_emails=["new@example.com"],
        match_status="ok",
        match_info="exact",
        status="active",
        issues=[],
    )

    records = store.list_agreements()
    assert len(records) == 1
    assert records[0].status == "active"
    assert records[0].client_emails == ["new@example.com"]
    assert len(store.installments_for_reminders()) == 7


def test_closed_stops_generating_and_does_not_reopen(tmp_path):
    store = _store(tmp_path)
    extracted = make_extracted_agreement()
    store.upsert(
        extracted,
        client_emails=["a@example.com"],
        match_status="ok",
        match_info="exact",
        status="active",
        issues=[],
    )

    assert store.close(extracted.case_number, "the client confirmed the payment") is True
    assert store.installments_for_reminders() == []

    # reprocessing the draft does NOT resurrect the agreement
    status = store.upsert(
        extracted,
        client_emails=["a@example.com"],
        match_status="ok",
        match_info="exact",
        status="active",
        issues=[],
    )
    assert status == "closed"
    assert store.installments_for_reminders() == []
    assert store.list_agreements()[0].closed_reason == "the client confirmed the payment"


def test_closing_a_nonexistent_case_returns_false(tmp_path):
    assert _store(tmp_path).close(CASE_MISSING, "x") is False


def test_reopen_an_agreement_closed_by_mistake(tmp_path):
    store = _store(tmp_path)
    extracted = make_extracted_agreement()
    store.upsert(
        extracted,
        client_emails=["a@example.com"],
        match_status="ok",
        match_info="exact",
        status="active",
        issues=[],
    )
    store.close(extracted.case_number, "mistake")

    assert store.reopen(extracted.case_number) is True
    record = store.find(extracted.case_number)
    assert record.status == "active"
    assert record.closed_reason is None
    assert len(store.installments_for_reminders()) == 7
    assert store.reopen(CASE_MISSING) is False


# ------------------------------------------- the collection hold ----------


def test_hold_is_released_by_a_settlement_and_by_closing(tmp_path):
    store = _store(tmp_path)
    extracted = make_extracted_agreement()
    store.upsert(
        extracted,
        client_emails=["finance@example.com"],
        match_status="ok",
        match_info="exact",
        status="active",
        issues=[],
    )
    case = extracted.case_number

    assert store.hold_collection(case, "receipt without a matching installment")
    assert not store.hold_collection(case, "again")  # idempotent
    assert [(c, r) for c, r, _ in store.holds()] == [
        (case, "receipt without a matching installment")
    ]

    # a settlement is the reconciliation the hold was waiting for
    store.settle_installment(f"{case}#O1P1", case, "manual", "checked")
    assert store.holds() == []

    store.hold_collection(case, "another attachment")
    store.close(case, "manual")
    assert store.holds() == []

    assert not store.release_collection(case)  # nothing to release


def test_manual_release(tmp_path):
    store = _store(tmp_path)
    store.hold_collection(CASE_OTHER, "a .heic attachment")
    assert store.release_collection(CASE_OTHER)
    assert store.holds() == []


def test_unsettle_reverts_a_settlement(tmp_path):
    store = _store(tmp_path)
    assert store.settle_installment(f"{CASE_OTHER}#O1P1", CASE_OTHER, "manual", "x")
    assert not store.settle_installment(f"{CASE_OTHER}#O1P1", CASE_OTHER, "manual", "again")
    assert store.unsettle(f"{CASE_OTHER}#O1P1")
    assert not store.unsettle(f"{CASE_OTHER}#O1P1")


# ---------------------------------------------- the notice's thread ---------


def test_notice_thread_is_stored_and_reaches_the_installments(tmp_path):
    store = _store(tmp_path)
    extracted = make_extracted_agreement()
    store.upsert(
        extracted,
        client_emails=["finance@example.com"],
        match_status="ok",
        match_info="exact",
        status="active",
        issues=[],
    )
    assert store.without_thread() == [extracted.case_number]
    assert store.installments_for_reminders()[0].agreement.thread_id == ""

    assert store.set_thread(extracted.case_number, "conv-abc")
    assert not store.set_thread(extracted.case_number, "")  # empty does not write
    assert not store.set_thread(CASE_MISSING, "x")  # nonexistent

    assert store.without_thread() == []
    assert store.installments_for_reminders()[0].agreement.thread_id == "conv-abc"
