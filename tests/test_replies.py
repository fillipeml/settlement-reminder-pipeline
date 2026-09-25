"""Handling of replies: settlement per installment through a receipt."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from settlement_reminders.ingest import AgreementStore
from settlement_reminders.ingest.mailbox import Attachment, MailboxMessage
from settlement_reminders.ingest.receipt import ExtractedReceipt, match_installment
from settlement_reminders.ingest.replies import extract_case_number, handle_reply
from tests.factories import CASE_EXTRACTED, make_extracted_agreement

CASE = CASE_EXTRACTED


def _store_with_agreement(tmp_path, status="active", emails=("finance@example.com",)):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    store.upsert(
        make_extracted_agreement(),
        client_emails=list(emails),
        match_status="ok",
        match_info="exact",
        status=status,
        issues=[],
    )
    return store


def _reply(sender="finance@example.com", subject=None):
    return MailboxMessage(
        id="r1",
        subject=subject
        if subject is not None
        else f"RES: LEMBRETE DE PAGAMENTO DA 1ª PARCELA DE ACORDO - {CASE} - A X B",
        sender=sender,
        received_at="2026-07-07T09:00:00Z",
    )


def _receipt(**over) -> ExtractedReceipt:
    data = dict(
        is_receipt=True,
        amount=Decimal("3200.00"),
        paid_on=date(2026, 6, 12),
        beneficiary="Fulano Comprador da Silva",
    )
    data.update(over)
    return ExtractedReceipt(**data)


class _FakeReader:
    """Returns a fixed ExtractedReceipt per attachment name."""

    def __init__(self, by_name):
        self._by_name = by_name

    def read(self, content, *, name=""):
        answer = self._by_name[name]
        if isinstance(answer, Exception):
            raise answer
        return answer


_ATTACHMENT = Attachment(name="receipt.pdf", content=b"%PDF fake")


def test_extracts_the_case_number_from_the_subject():
    assert extract_case_number(f"RES: reminder {CASE} - x") == CASE
    assert extract_case_number("any subject without a case") == ""
    assert extract_case_number("") == ""


# ------------------------------------------------ receipt -> settlement ----


def test_receipt_settles_the_installment_and_the_agreement_stays_active(tmp_path):
    store = _store_with_agreement(tmp_path)
    reader = _FakeReader({"receipt.pdf": _receipt()})

    result = handle_reply(_reply(), store, attachments=[_ATTACHMENT], reader=reader)

    assert result.action == "settled"
    assert result.settled == [f"{CASE}#O1P1"]  # the 1st open one of the same amount
    assert store.find(CASE).status == "active"  # 6 installments remain
    assert f"{CASE}#O1P1" in store.settled_installments(CASE)


def test_equal_receipts_settle_successive_installments(tmp_path):
    store = _store_with_agreement(tmp_path)
    reader = _FakeReader({"receipt.pdf": _receipt()})

    r1 = handle_reply(_reply(), store, attachments=[_ATTACHMENT], reader=reader)
    msg2 = _reply()
    msg2.id = "r2"
    r2 = handle_reply(msg2, store, attachments=[_ATTACHMENT], reader=reader)

    assert r1.settled == [f"{CASE}#O1P1"]
    assert r2.settled == [f"{CASE}#O1P2"]


def test_last_installment_settled_closes_the_agreement(tmp_path):
    store = _store_with_agreement(tmp_path)
    # a manual settlement of all but the last (the final installment to the attorney)
    for i in range(1, 7):
        store.settle_installment(f"{CASE}#O1P{i}", CASE, "manual", "test")
    reader = _FakeReader({"receipt.pdf": _receipt(amount=Decimal("4800.00"))})

    result = handle_reply(_reply(), store, attachments=[_ATTACHMENT], reader=reader)

    assert result.action == "closed"
    assert "every installment settled" in result.detail
    assert store.find(CASE).status == "closed"
    assert store.installments_for_reminders() == []


def test_reply_without_attachment_no_longer_closes(tmp_path):
    # a deliberate change: only a receipt settles/closes
    store = _store_with_agreement(tmp_path)

    result = handle_reply(_reply(), store)

    assert result.action == "check"
    assert "without an attached receipt" in result.detail
    assert store.find(CASE).status == "active"


def test_amount_that_does_not_match_becomes_a_check(tmp_path):
    store = _store_with_agreement(tmp_path)
    reader = _FakeReader({"receipt.pdf": _receipt(amount=Decimal("999.99"))})

    result = handle_reply(_reply(), store, attachments=[_ATTACHMENT], reader=reader)

    assert result.action == "check"
    assert "matches no open installment" in result.detail
    assert store.settled_installments(CASE) == set()


def test_document_that_is_not_a_receipt_becomes_a_check(tmp_path):
    store = _store_with_agreement(tmp_path)
    reader = _FakeReader({"receipt.pdf": _receipt(is_receipt=False, amount=None)})

    result = handle_reply(_reply(), store, attachments=[_ATTACHMENT], reader=reader)

    assert result.action == "check"
    assert store.settled_installments(CASE) == set()


def test_a_scheduling_does_not_settle(tmp_path):
    store = _store_with_agreement(tmp_path)
    reader = _FakeReader({"receipt.pdf": _receipt(scheduled=True)})

    result = handle_reply(_reply(), store, attachments=[_ATTACHMENT], reader=reader)

    assert result.action == "check"
    assert store.settled_installments(CASE) == set()


def test_the_lawyer_may_forward_the_receipt(tmp_path):
    store = _store_with_agreement(tmp_path)
    reader = _FakeReader({"receipt.pdf": _receipt()})

    result = handle_reply(
        _reply(sender="lawyer@lawfirm.example"), store, attachments=[_ATTACHMENT], reader=reader
    )

    assert result.action == "settled"


def test_without_a_reader_becomes_a_check(tmp_path):
    store = _store_with_agreement(tmp_path)

    result = handle_reply(_reply(), store, attachments=[_ATTACHMENT], reader=None)

    assert result.action == "check"
    assert "unavailable" in result.detail


def test_a_reading_failure_propagates_to_the_caller(tmp_path):
    store = _store_with_agreement(tmp_path)
    reader = _FakeReader({"receipt.pdf": RuntimeError("API down")})

    try:
        handle_reply(_reply(), store, attachments=[_ATTACHMENT], reader=reader)
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("a reading error should propagate (retry)")


# ------------------------------------------------------ classic rules ----


def test_unknown_sender_does_not_settle(tmp_path):
    store = _store_with_agreement(tmp_path)

    result = handle_reply(_reply(sender="someone@otherdomain.com"), store)

    assert result.action == "check"
    assert "unrecognised" in result.detail
    assert store.find(CASE).status == "active"


def test_sender_is_compared_case_insensitively(tmp_path):
    store = _store_with_agreement(tmp_path, emails=("Finance@Example.com",))
    reader = _FakeReader({"receipt.pdf": _receipt()})

    result = handle_reply(
        _reply(sender="finance@example.com"), store, attachments=[_ATTACHMENT], reader=reader
    )

    assert result.action == "settled"


def test_case_outside_the_database_becomes_a_check(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))

    result = handle_reply(_reply(), store)

    assert result.action == "unknown_agreement"
    assert CASE in result.detail


def test_already_closed_agreement_generates_no_action(tmp_path):
    store = _store_with_agreement(tmp_path)
    store.close(CASE, "the client paid")

    result = handle_reply(_reply(), store)

    assert result.action == "already_closed"


def test_message_without_a_case_is_ignored(tmp_path):
    store = _store_with_agreement(tmp_path)

    result = handle_reply(_reply(subject="Legal newsletter"), store)

    assert result.action == "ignored"


# --------------------------------------------------------- match_installment ----


def test_match_installment_prefers_the_oldest_open_due_date():
    from settlement_reminders.ingest.convert import installments_from_extracted

    every = installments_from_extracted(make_extracted_agreement(), ["a@x.com"])
    receipt = _receipt()

    first = match_installment(receipt, every, settled=set())
    assert first.external_id == f"{CASE}#O1P1"

    second = match_installment(receipt, every, settled={first.external_id})
    assert second.external_id == f"{CASE}#O1P2"

    assert match_installment(_receipt(amount=Decimal("1.00")), every, set()) is None


# --------------------------- an illegible attachment in the middle ----------------


def _http_response(status: int):
    import httpx

    return httpx.Response(status, request=httpx.Request("POST", "https://x"))


def test_an_illegible_attachment_does_not_prevent_settling_the_others(tmp_path):
    """Regression: an e-mail signature brought the message down.

    The client replied with two real receipts and, in the middle, an 'image.png' that was
    a JPEG. The API refused it (400), the exception aborted the whole message and the
    receipts were never read: the client was collected the next day, having paid. A defect
    of one file cannot contaminate the other attachments.
    """
    from anthropic import BadRequestError

    store = _store_with_agreement(tmp_path)
    bad = Attachment(name="image.png", content=b"\xff\xd8\xff fake jpeg")
    failure = BadRequestError(
        message="media type mismatch", response=_http_response(400), body=None
    )
    reader = _FakeReader({"image.png": failure, "receipt.pdf": _receipt()})

    r = handle_reply(_reply(), store, attachments=[bad, _ATTACHMENT], reader=reader)

    assert r.action == "settled"
    assert r.settled, "the valid receipt of the same e-mail must settle"
    assert "image.png" in r.detail  # the illegible attachment is recorded


def test_a_transient_failure_still_propagates(tmp_path):
    """Unavailability/rate limit must still be retried on the next run."""
    from anthropic import InternalServerError

    store = _store_with_agreement(tmp_path)
    failure = InternalServerError(message="overloaded", response=_http_response(500), body=None)
    reader = _FakeReader({"receipt.pdf": failure})
    try:
        handle_reply(_reply(), store, attachments=[_ATTACHMENT], reader=reader)
    except InternalServerError:
        pass
    else:  # pragma: no cover
        raise AssertionError("a transient failure should propagate for a retry")
    assert not store.settled_installments(CASE)


# ---------------------- collection hold: an attachment that did not settle -----------
# The lesson: a receipt arrived, was not read, and the collection went out the next day.
# Rule: a client attachment that did not settle and was not discarded as a "non-receipt"
# HOLDS collection and escalation until a human reconciles.


def test_genuine_receipt_without_a_matching_installment_holds(tmp_path):
    """A real payment with an amount that does not match (e.g. two installments in one
    transfer): no collection until someone reconciles."""
    store = _store_with_agreement(tmp_path)
    reader = _FakeReader({"receipt.pdf": _receipt(amount=Decimal("6400.00"))})

    r = handle_reply(_reply(), store, attachments=[_ATTACHMENT], reader=reader)

    assert r.action == "check"
    assert not r.settled
    assert "6400.00" in r.hold and "without a matching installment" in r.hold
    assert "ON HOLD" in r.detail


def test_document_that_is_not_a_receipt_does_not_hold(tmp_path):
    """The model recognised it is not a payment (a contract, a scheduling): the normal cycle
    follows: check, but without locking the collection."""
    store = _store_with_agreement(tmp_path)
    for doc in (_receipt(is_receipt=False), _receipt(scheduled=True)):
        reader = _FakeReader({"receipt.pdf": doc})
        r = handle_reply(_reply(), store, attachments=[_ATTACHMENT], reader=reader)
        assert r.action == "check"
        assert r.hold == ""


def test_illegible_attachment_holds(tmp_path):
    from anthropic import BadRequestError

    store = _store_with_agreement(tmp_path)
    failure = BadRequestError(message="invalid type", response=_http_response(400), body=None)
    reader = _FakeReader({"receipt.pdf": failure})

    r = handle_reply(_reply(), store, attachments=[_ATTACHMENT], reader=reader)

    assert r.action == "check"
    assert "illegible" in r.hold and "receipt.pdf" in r.hold


def test_unsupported_attachment_type_holds(tmp_path):
    """The mailbox discarded a .heic (iPhone) or a .zip: it may be the receipt. No legible
    attachment, but a discarded one -> hold."""
    store = _store_with_agreement(tmp_path)
    msg = _reply()
    msg.ignored = ["IMG_2041.heic", "receipts.zip"]

    r = handle_reply(msg, store, attachments=[], reader=_FakeReader({}))

    assert r.action == "check"
    assert "IMG_2041.heic" in r.hold and "receipts.zip" in r.hold


def test_signature_attachments_do_not_hold(tmp_path):
    """A discarded vCard/ics/S-MIME signature is not a receipt: it is a reply without an
    attachment, as always."""
    store = _store_with_agreement(tmp_path)
    msg = _reply()
    msg.ignored = ["Someone.vcf", "invite.ics", "smime.p7s"]

    r = handle_reply(msg, store, attachments=[], reader=_FakeReader({}))

    assert r.action == "check"
    assert r.hold == ""
    assert "without an attached receipt" in r.detail


def test_without_a_reader_with_an_attachment_holds(tmp_path):
    store = _store_with_agreement(tmp_path)
    r = handle_reply(_reply(), store, attachments=[_ATTACHMENT], reader=None)
    assert r.action == "check"
    assert "receipt.pdf" in r.hold


def test_partial_settlement_with_a_genuine_unmatched_receipt_still_holds(tmp_path):
    """Two receipts: one matched, the other is a real payment without an installment. The
    settlement happens AND the collection of the rest stays on hold for reconciliation."""
    store = _store_with_agreement(tmp_path)
    other = Attachment(name="other.pdf", content=b"%PDF fake 2")
    reader = _FakeReader({"receipt.pdf": _receipt(), "other.pdf": _receipt(amount=Decimal("1.00"))})

    r = handle_reply(_reply(), store, attachments=[_ATTACHMENT, other], reader=reader)

    assert r.action == "settled"
    assert len(r.settled) == 1
    assert "other.pdf" in r.hold
