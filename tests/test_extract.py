"""Draft extraction (the legacy path): the model's API is stubbed, no network."""

from __future__ import annotations

import base64
from decimal import Decimal
from types import SimpleNamespace

import pytest

from settlement_reminders.ingest import AgreementExtractor, ExtractedAgreement, ExtractionError
from settlement_reminders.ingest.extract import check_consistency
from settlement_reminders.ingest.receipt import media_type
from tests.factories import CASE_EXTRACTED, make_extracted_agreement


class _StubMessages:
    def __init__(self, agreement: ExtractedAgreement | None) -> None:
        self._agreement = agreement
        self.kwargs: dict | None = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(parsed_output=self._agreement, stop_reason="end_turn")


class _StubClient:
    def __init__(self, agreement: ExtractedAgreement | None) -> None:
        self.messages = _StubMessages(agreement)


def test_extracts_a_pdf_and_builds_the_right_request():
    agreement = make_extracted_agreement()
    client = _StubClient(agreement)
    extractor = AgreementExtractor(client=client)

    pdf = b"%PDF-1.4 fictional content"
    result = extractor.extract_pdf(pdf, file_name="draft.pdf")

    assert result.case_number == CASE_EXTRACTED
    assert result.confidence == "high"
    assert result.doubts == []

    kwargs = client.messages.kwargs
    assert kwargs["model"] == "claude-sonnet-5"
    assert kwargs["output_format"] is ExtractedAgreement
    doc, text = kwargs["messages"][0]["content"]
    assert doc["type"] == "document"
    assert doc["source"]["media_type"] == "application/pdf"
    assert base64.standard_b64decode(doc["source"]["data"]) == pdf
    assert text["type"] == "text"


def test_consistency_doubts_lower_the_confidence():
    agreement = make_extracted_agreement(case_number="invalid case", confidence="high")
    extractor = AgreementExtractor(client=_StubClient(agreement))

    result = extractor.extract_pdf(b"%PDF-1.4 x")

    assert any("CNJ pattern" in d for d in result.doubts)
    assert result.confidence == "medium"


def test_without_a_key_and_without_a_client_fails_early():
    with pytest.raises(ExtractionError, match="ANTHROPIC_API_KEY"):
        AgreementExtractor(api_key="")


def test_extraction_without_parsed_output_is_an_error():
    extractor = AgreementExtractor(client=_StubClient(None))
    with pytest.raises(ExtractionError, match="returned no data"):
        extractor.extract_pdf(b"%PDF-1.4 x")


def test_checks_catch_a_broken_schedule():
    out_of_order = make_extracted_agreement()
    installments = out_of_order.obligations[0].installments
    installments[1], installments[2] = installments[2], installments[1]
    assert any("out of order" in p for p in check_consistency(out_of_order))

    repeated = make_extracted_agreement()
    repeated.obligations[0].installments[1].number = 1
    assert any("repeated" in p for p in check_consistency(repeated))

    zero = make_extracted_agreement()
    zero.obligations[0].installments[0].amount = Decimal("0")
    assert any("invalid amount" in p for p in check_consistency(zero))


def test_checks_catch_incomplete_obligations():
    none = make_extracted_agreement(obligations=[])
    assert any("no payment obligation" in p for p in check_consistency(none))

    no_installments = make_extracted_agreement()
    no_installments.obligations[1].installments = []
    assert any("without installments" in p for p in check_consistency(no_installments))

    no_details = make_extracted_agreement()
    no_details.obligations[0].payment_details = "  "
    assert any("without bank details" in p for p in check_consistency(no_details))


def test_checks_catch_a_sum_above_the_total():
    agreement = make_extracted_agreement(total_amount=Decimal("10000.00"))
    assert any("exceeds the total amount" in p for p in check_consistency(agreement))


def test_consistent_agreement_generates_no_doubts():
    assert check_consistency(make_extracted_agreement()) == []


def test_media_type_comes_from_the_bytes_not_the_extension():
    assert media_type(b"\xff\xd8\xff...", "image.png") == "image/jpeg"
    assert media_type(b"%PDF-1.4", "receipt.jpg") == "application/pdf"
    assert media_type(b"\x89PNG\r\n\x1a\n", "x") == "image/png"
    assert media_type(b"RIFF....WEBPVP8 ", "x") == "image/webp"
    assert media_type(b"garbage", "photo.jpeg") == "image/jpeg"  # the extension only as a fallback
    assert media_type(b"garbage", "archive.zip") is None
