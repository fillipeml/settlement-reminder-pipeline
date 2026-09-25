"""The draft ingestion pipeline (extraction + recipient + store, all faked)."""

from __future__ import annotations

from settlement_reminders.ingest import AgreementStore, ClientContact, MatchResult, process_draft
from tests.factories import make_extracted_agreement


class FakeExtractor:
    def __init__(self, extracted):
        self._extracted = extracted

    def extract_pdf(self, pdf, *, file_name=""):
        return self._extracted


class FakeMatcher:
    def __init__(self, result):
        self._result = result

    def match(self, name):
        return self._result


def match_ok() -> MatchResult:
    return MatchResult(
        status="ok",
        contact=ClientContact(name="CONSTRUTORA EXEMPLO LTDA", emails=["finance@example.com"]),
        confidence="high",
        rationale="exact match",
    )


def test_confident_extraction_and_ok_match_activate_the_agreement(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))

    result = process_draft(
        b"%PDF-1.4 fake",
        extractor=FakeExtractor(make_extracted_agreement()),
        matcher=FakeMatcher(match_ok()),
        store=store,
        origin="draft.pdf",
    )

    assert result.status == "active"
    assert result.issues == []
    assert result.client_emails == ["finance@example.com"]
    assert len(store.installments_for_reminders()) == 7


def test_recipient_without_email_leaves_it_pending(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    match = MatchResult(
        status="no_email",
        contact=ClientContact(name="CONSTRUTORA EXEMPLO LTDA", emails=[]),
        confidence="high",
        rationale="exact name match",
    )

    result = process_draft(
        b"%PDF-1.4 fake",
        extractor=FakeExtractor(make_extracted_agreement()),
        matcher=FakeMatcher(match),
        store=store,
    )

    assert result.status == "pending"
    assert any("no_email" in i for i in result.issues)
    assert store.installments_for_reminders() == []


def test_extraction_with_a_doubt_leaves_it_pending_even_with_an_ok_match(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    extracted = make_extracted_agreement(
        confidence="medium", doubts=["the amount of the 3rd installment is illegible in the PDF"]
    )

    result = process_draft(
        b"%PDF-1.4 fake",
        extractor=FakeExtractor(extracted),
        matcher=FakeMatcher(match_ok()),
        store=store,
    )

    assert result.status == "pending"
    assert any("medium confidence" in i for i in result.issues)
    assert any("illegible" in i for i in result.issues)
    assert store.installments_for_reminders() == []


def test_the_draft_path_becomes_the_origin(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    pdf = tmp_path / "SETTLEMENT - X.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    result = process_draft(
        pdf,
        extractor=FakeExtractor(make_extracted_agreement()),
        matcher=FakeMatcher(match_ok()),
        store=store,
    )

    assert result.origin == "SETTLEMENT - X.pdf"
    assert store.list_agreements()[0].origin == "SETTLEMENT - X.pdf"
