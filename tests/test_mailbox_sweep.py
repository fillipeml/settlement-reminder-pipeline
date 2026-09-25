"""The mailbox sweep (process_mailbox) and the guards of the run."""

from __future__ import annotations

from decimal import Decimal

from settlement_reminders.config import Settings
from settlement_reminders.ingest import AgreementStore
from settlement_reminders.ingest.mailbox import Attachment, MailboxMessage
from settlement_reminders.ingest.pipeline import process_mailbox
from settlement_reminders.ingest.receipt import ExtractedReceipt
from settlement_reminders.run import Summary, _ingest_mailbox
from tests.factories import CASE_NOTICE, make_extracted_agreement, make_notice
from tests.test_pipeline import FakeExtractor, FakeMatcher, match_ok


class FakeReader:
    def __init__(self, messages, attachments, bodies=None):
        self._messages = messages
        self._attachments = attachments
        self._bodies = bodies or {}
        self.downloads = 0  # how many times attachments were downloaded
        self.bodies_read = 0  # how many times a body was fetched

    def recent_messages(self, *, days=7, only_with_attachments=False):
        return self._messages

    def pdf_attachments(self, message):
        self.downloads += 1
        return self._attachments.get(message.id, [])

    def body(self, message):
        self.bodies_read += 1
        body = self._bodies.get(message.id, "")
        if isinstance(body, Exception):
            raise body
        return body


class FakeReaderWithoutBody(FakeReader):
    """A legacy reader, without the body() method."""

    body = None


class FakeSender:
    """Captures sends; can fail from the N-th send on."""

    def __init__(self, *, dry_run=False, fail_at=None):
        self.dry_run = dry_run
        self.fail_at = fail_at  # 1-based; None = never fails
        self.sent = []

    def send(self, *, to, subject, html, cc=None, inline_images=None):
        if self.fail_at is not None and len(self.sent) + 1 >= self.fail_at:
            raise RuntimeError("Graph sendMail unavailable")
        self.sent.append({"to": list(to), "cc": list(cc or []), "subject": subject, "html": html})


class FailingExtractor:
    def extract_pdf(self, pdf, *, file_name=""):
        raise RuntimeError("API unavailable")


class FixedReader:
    def __init__(self, receipt):
        self._r = receipt

    def read(self, content, *, name=""):
        if isinstance(self._r, Exception):
            raise self._r
        return self._r


def receipt_ok(amount="3200.00"):
    return ExtractedReceipt(is_receipt=True, amount=Decimal(amount))


def _msg(msg_id="m1"):
    return MailboxMessage(
        id=msg_id,
        subject="FW: Settlement",
        sender="controllership@lawfirm.example",
        received_at="2026-07-06T12:00:00Z",
    )


def _kwargs(store, **over):
    base = dict(
        extractor=FakeExtractor(make_extracted_agreement()),
        matcher=FakeMatcher(match_ok()),
        store=store,
    )
    base.update(over)
    return base


def test_sweep_processes_and_is_idempotent(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    reader = FakeReader([_msg()], {"m1": [Attachment(name="draft.pdf", content=b"%PDF fake")]})
    kwargs = _kwargs(store)

    r1 = process_mailbox(reader, **kwargs)
    assert r1.messages == 1
    assert r1.drafts == 1
    assert r1.active == 1
    assert r1.errors == []
    assert len(store.installments_for_reminders()) == 7

    r2 = process_mailbox(reader, **kwargs)
    assert r2.already_processed == 1
    assert r2.drafts == 0


def test_message_without_pdf_is_recorded_and_not_reprocessed(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    msg = _msg()
    msg.ignored.append("photo.png")
    reader = FakeReader([msg], {})  # no PDF attachments
    kwargs = _kwargs(store)

    r1 = process_mailbox(reader, **kwargs)
    assert r1.no_pdf == 1

    r2 = process_mailbox(reader, **kwargs)
    assert r2.already_processed == 1
    assert r2.no_pdf == 0


def test_extraction_failure_does_not_mark_the_message_and_retries(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    reader = FakeReader([_msg()], {"m1": [Attachment(name="draft.pdf", content=b"%PDF fake")]})

    r1 = process_mailbox(reader, **_kwargs(store, extractor=FailingExtractor()))
    assert len(r1.errors) == 1
    assert r1.drafts == 0

    # the next run tries again (the message was NOT marked as processed)
    r2 = process_mailbox(reader, **_kwargs(store))
    assert r2.already_processed == 0
    assert r2.drafts == 1
    assert r2.active == 1


def _active_store(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    extracted = make_extracted_agreement()
    store.upsert(
        extracted,
        client_emails=["finance@example.com"],
        match_status="ok",
        match_info="exact",
        status="active",
        issues=[],
    )
    return store, extracted


def test_client_reply_without_a_receipt_becomes_a_check(tmp_path):
    # a deliberate change (the collection route): a text reply no longer closes the
    # agreement; only a validated receipt settles/closes
    store, extracted = _active_store(tmp_path)
    reply = MailboxMessage(
        id="r1",
        subject=f"RES: LEMBRETE DE PAGAMENTO - {extracted.case_number} - A X B",
        sender="finance@example.com",
        received_at="2026-07-07T09:00:00Z",
    )
    reader = FakeReader([reply], {})

    summary = process_mailbox(reader, **_kwargs(store))

    assert len(summary.to_check) == 1
    assert summary.closed == []
    assert store.find(extracted.case_number).status == "active"

    # idempotency: the same reply is not handled twice
    r2 = process_mailbox(reader, **_kwargs(store))
    assert r2.already_processed == 1
    assert r2.to_check == []


def test_pdf_receipt_settles_the_installment_through_the_mailbox(tmp_path):
    store, extracted = _active_store(tmp_path)
    receipt = MailboxMessage(
        id="c1",
        subject=f"RES: LEMBRETE - {extracted.case_number} - receipt attached",
        sender="finance@example.com",
        received_at="2026-07-07T10:00:00Z",
        has_attachments=True,
    )
    reader = FakeReader([receipt], {"c1": [Attachment(name="receipt.pdf", content=b"%PDF fake")]})

    summary = process_mailbox(
        reader,
        **_kwargs(store, extractor=FailingExtractor(), receipt_reader=FixedReader(receipt_ok())),
    )

    assert summary.drafts == 0
    assert len(summary.settled) == 1
    assert summary.closed == []
    assert store.find(extracted.case_number).status == "active"  # 6 installments remain
    assert f"{extracted.case_number}#O1P1" in store.settled_installments(extracted.case_number)


def test_a_receipt_reading_failure_retries(tmp_path):
    store, extracted = _active_store(tmp_path)
    msg = MailboxMessage(
        id="c9",
        subject=f"RES: {extracted.case_number} - receipt",
        sender="finance@example.com",
        received_at="2026-07-07T10:00:00Z",
        has_attachments=True,
    )
    attachments = {"c9": [Attachment(name="receipt.pdf", content=b"%PDF fake")]}

    r1 = process_mailbox(
        FakeReader([msg], attachments),
        **_kwargs(
            store,
            extractor=FailingExtractor(),
            receipt_reader=FixedReader(RuntimeError("API down")),
        ),
    )
    assert len(r1.errors) == 1
    assert not store.message_processed("c9")

    r2 = process_mailbox(
        FakeReader([msg], attachments),
        **_kwargs(store, extractor=FailingExtractor(), receipt_reader=FixedReader(receipt_ok())),
    )
    assert len(r2.settled) == 1
    assert store.message_processed("c9")


def test_reply_from_a_strange_sender_goes_to_check(tmp_path):
    store, extracted = _active_store(tmp_path)
    reply = MailboxMessage(
        id="r2",
        subject=f"RES: {extracted.case_number}",
        sender="curious@anywhere.com",
        received_at="2026-07-07T11:00:00Z",
    )

    summary = process_mailbox(FakeReader([reply], {}), **_kwargs(store))

    assert len(summary.to_check) == 1
    assert summary.closed == []
    assert store.find(extracted.case_number).status == "active"


def test_pdf_from_an_external_sender_is_not_a_draft(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    msg = MailboxMessage(
        id="ext1",
        subject="Energy bill",
        sender="client@supplier.example",
        received_at="2026-07-14T09:00:00Z",
        has_attachments=True,
    )
    reader = FakeReader([msg], {"ext1": [Attachment(name="bill.pdf", content=b"%PDF fake")]})

    summary = process_mailbox(
        reader, **_kwargs(store, extractor=FailingExtractor(), allowed_senders=["@lawfirm.example"])
    )

    assert summary.refused_sender == 1
    assert summary.drafts == 0
    assert summary.errors == []
    assert reader.downloads == 0, "a refused attachment must not even be downloaded"
    assert store.list_agreements() == []

    # and the message is recorded (not reprocessed on the next sweep)
    r2 = process_mailbox(
        reader, **_kwargs(store, extractor=FailingExtractor(), allowed_senders=["@lawfirm.example"])
    )
    assert r2.already_processed == 1


def test_pdf_from_group_traffic_is_not_a_draft_even_from_an_internal_sender(tmp_path):
    # a lawyer's e-mail to a group the account belongs to lands in the bot's inbox without
    # addressing it: not a draft, no extraction/cost
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    msg = MailboxMessage(
        id="grp1",
        subject="Contract for the team's review",
        sender="lawyer@lawfirm.example",
        received_at="2026-07-14T09:00:00Z",
        has_attachments=True,
        addressed_to_bot=False,
    )
    reader = FakeReader([msg], {"grp1": [Attachment(name="contract.pdf", content=b"%PDF fake")]})

    summary = process_mailbox(
        reader, **_kwargs(store, extractor=FailingExtractor(), allowed_senders=["@lawfirm.example"])
    )

    assert summary.refused_sender == 1
    assert summary.drafts == 0
    assert reader.downloads == 0
    assert store.list_agreements() == []


def test_internal_sender_keeps_submitting_drafts(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    reader = FakeReader([_msg()], {"m1": [Attachment(name="draft.pdf", content=b"%PDF fake")]})

    summary = process_mailbox(reader, **_kwargs(store, allowed_senders=["@lawfirm.example"]))

    assert summary.drafts == 1
    assert summary.active == 1


def test_external_client_reply_bypasses_the_draft_filter(tmp_path):
    store, extracted = _active_store(tmp_path)
    reply = MailboxMessage(
        id="r9",
        subject=f"RES: {extracted.case_number} - receipt",
        sender="finance@example.com",
        received_at="2026-07-14T10:00:00Z",
        has_attachments=True,
    )
    reader = FakeReader([reply], {"r9": [Attachment(name="receipt.pdf", content=b"%PDF fake")]})

    summary = process_mailbox(
        reader,
        **_kwargs(
            store,
            extractor=FailingExtractor(),
            allowed_senders=["@lawfirm.example"],
            receipt_reader=FixedReader(receipt_ok()),
        ),
    )

    assert summary.refused_sender == 0
    assert len(summary.settled) == 1  # the draft filter does not block replies
    assert store.find(extracted.case_number).status == "active"


# ------------------------------------------ the skill's notices in the mailbox ----


def _notice_msg(msg_id="k1", **over):
    fields = dict(
        id=msg_id,
        subject=f"ACORDO PACTUADO - {CASE_NOTICE} - FULANO X EXEMPLO",
        sender="lawyer@lawfirm.example",
        received_at="2026-08-31T09:00:00Z",
    )
    fields.update(over)
    return MailboxMessage(**fields)


def _notice_kwargs(store, **over):
    return _kwargs(
        store, extractor=FailingExtractor(), allowed_senders=["@lawfirm.example"], **over
    )


def test_notice_in_the_mailbox_registers_without_a_model(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    reader = FakeReader([_notice_msg()], {}, bodies={"k1": make_notice()})

    summary = process_mailbox(reader, **_notice_kwargs(store))

    assert summary.notices == 1
    assert summary.active == 1
    assert summary.errors == []
    assert reader.downloads == 0
    assert len(store.installments_for_reminders()) == 3

    # idempotency: a second sweep neither reprocesses nor re-reads the body
    read = reader.bodies_read
    r2 = process_mailbox(reader, **_notice_kwargs(store))
    assert r2.already_processed == 1
    assert r2.notices == 0
    assert reader.bodies_read == read


def test_correction_of_a_known_case_is_not_treated_as_a_reply(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    process_mailbox(
        FakeReader([_notice_msg()], {}, bodies={"k1": make_notice()}), **_notice_kwargs(store)
    )

    correction = make_notice(
        OPERATION="OPERATION: CORRECTION", CLIENT_EMAILS="CLIENT_EMAILS: corrected@exemplo.example"
    )
    summary = process_mailbox(
        FakeReader([_notice_msg("k2")], {}, bodies={"k2": correction}), **_notice_kwargs(store)
    )

    assert summary.notices == 1
    assert summary.closed == []  # not treated as a client reply
    record = store.find(CASE_NOTICE)
    assert record.status == "active"
    assert record.client_emails == ["corrected@exemplo.example"]


def test_invalid_notice_goes_to_check_and_is_not_reprocessed(tmp_path):
    body = "=== AUTOMATION-DATA v1 ===\nOPERATION: REGISTER\n=== END AUTOMATION-DATA ==="  # no CASE/CLIENT/...
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    reader = FakeReader([_notice_msg()], {}, bodies={"k1": body})

    summary = process_mailbox(reader, **_notice_kwargs(store))
    assert len(summary.to_check) == 1
    assert summary.notices == 0
    assert store.list_agreements() == []

    r2 = process_mailbox(reader, **_notice_kwargs(store))
    assert r2.already_processed == 1  # a deterministic defect: not retried


def test_test_notice_does_not_register_an_agreement(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    msg = _notice_msg(subject=f"[TEST] ACORDO PACTUADO - {CASE_NOTICE} - A X B")
    reader = FakeReader([msg], {}, bodies={"k1": make_notice()})

    summary = process_mailbox(reader, **_notice_kwargs(store))

    assert summary.notices == 0
    assert summary.errors == []
    assert store.list_agreements() == []  # no agreement registered
    assert store.message_processed("k1")  # not reprocessed


def test_reply_in_the_notice_thread_is_not_a_re_registration(tmp_path):
    # a real bug of the pilot: Outlook's reply QUOTES the original notice, including the
    # AUTOMATION-DATA block. Without the prefix filter the attached receipt would become a
    # re-registration and would never be read.
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    process_mailbox(
        FakeReader([_notice_msg("k1")], {}, bodies={"k1": make_notice()}),
        **_followup_kwargs(store, FakeSender()),
    )

    # the lawyer replies IN THE THREAD attaching the receipt; the body quotes the block
    reply = _notice_msg(
        "k2", subject=f"RE: ACORDO PACTUADO - {CASE_NOTICE} - A X B", has_attachments=True
    )
    reader = FakeReader(
        [reply],
        {"k2": [Attachment(name="receipt.pdf", content=b"%PDF fake")]},
        bodies={"k2": "<p>Receipt attached.</p>" + make_notice()},
    )
    summary = process_mailbox(
        reader, **_notice_kwargs(store, receipt_reader=FixedReader(receipt_ok("3000.00")))
    )

    assert summary.notices == 0  # did not re-register
    assert len(summary.settled) == 1  # read the receipt and settled
    assert reader.bodies_read <= 1  # the reply route does not even fetch the body


def test_notice_from_an_external_sender_is_not_read(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    reader = FakeReader(
        [_notice_msg(sender="someone@outside.example")], {}, bodies={"k1": make_notice()}
    )

    summary = process_mailbox(reader, **_notice_kwargs(store))

    assert reader.bodies_read == 0
    assert summary.notices == 0
    assert store.list_agreements() == []


def test_body_reading_failure_is_transient_and_retries(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    failing = FakeReader([_notice_msg()], {}, bodies={"k1": RuntimeError("Graph down")})
    r1 = process_mailbox(failing, **_notice_kwargs(store))
    assert len(r1.errors) == 1
    assert r1.notices == 0

    ok = FakeReader([_notice_msg()], {}, bodies={"k1": make_notice()})
    r2 = process_mailbox(ok, **_notice_kwargs(store))
    assert r2.already_processed == 0
    assert r2.notices == 1


def test_legacy_reader_without_body_keeps_working(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    reader = FakeReaderWithoutBody(
        [_msg()], {"m1": [Attachment(name="draft.pdf", content=b"%PDF fake")]}
    )

    summary = process_mailbox(reader, **_kwargs(store, allowed_senders=["@lawfirm.example"]))
    assert summary.drafts == 1
    assert summary.notices == 0


def test_forwarded_draft_without_a_block_goes_to_extraction(tmp_path):
    # a lawyer forwards a PDF draft without using the skill: a body without a block ->
    # the classic path (model extraction)
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    reader = FakeReader(
        [_msg()],
        {"m1": [Attachment(name="draft.pdf", content=b"%PDF fake")]},
        bodies={"m1": "<p>Draft attached for registration.</p>"},
    )

    summary = process_mailbox(reader, **_kwargs(store, allowed_senders=["@lawfirm.example"]))
    assert summary.notices == 0
    assert summary.drafts == 1
    assert summary.active == 1


# ------------------------------ follow-ups: forward to the client + confirmation ----


def _followup_kwargs(store, sender):
    return dict(
        extractor=FailingExtractor(),
        matcher=FakeMatcher(match_ok()),
        store=store,
        allowed_senders=["@lawfirm.example"],
        sender=sender,
        bot_mailbox="automation@lawfirm.example",
        confirmation_emails=["controllership@lawfirm.example"],
        cc_notices=["labour@lawfirm.example"],
        firm_name="Example Law Firm",
    )


def test_active_notice_is_forwarded_to_the_client_and_confirmed(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    sender = FakeSender()
    reader = FakeReader([_notice_msg()], {}, bodies={"k1": make_notice()})

    summary = process_mailbox(reader, **_followup_kwargs(store, sender))

    assert summary.notices == 1
    assert summary.errors == []
    assert len(sender.sent) == 2

    forward, confirmation = sender.sent
    # 1) the notice to the client: without the machine block, with the case in the subject
    assert forward["to"] == ["finance@exemplo.example", "board@exemplo.example"]
    assert "lawyer@lawfirm.example" in forward["cc"]
    assert "automation@lawfirm.example" in forward["cc"]
    # the controllership + CC_NOTICES also follow the notice
    assert "controllership@lawfirm.example" in forward["cc"]
    assert "labour@lawfirm.example" in forward["cc"]
    assert forward["subject"].startswith(f"ACORDO PACTUADO - {CASE_NOTICE}")
    assert "AUTOMATION-DATA" not in forward["html"]
    assert "automation data" not in forward["html"]
    assert "Example Law Firm" in forward["html"]
    # 2) the confirmation to the lawyer + controllership
    assert confirmation["to"] == ["lawyer@lawfirm.example", "controllership@lawfirm.example"]
    assert "registered" in confirmation["subject"]
    assert "Next reminder" in confirmation["html"]
    assert store.notice_sent(CASE_NOTICE)


def test_pending_notice_only_confirms_without_touching_the_client(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    sender = FakeSender()
    pending = make_notice(CLIENT_EMAILS="CLIENT_EMAILS:")  # becomes pending
    reader = FakeReader([_notice_msg()], {}, bodies={"k1": pending})

    summary = process_mailbox(reader, **_followup_kwargs(store, sender))

    assert summary.pending == 1
    assert len(sender.sent) == 1
    (confirmation,) = sender.sent
    assert "PENDING" in confirmation["subject"]
    assert "did NOT enter the automatic cycle" in confirmation["html"]
    assert not store.notice_sent(CASE_NOTICE)


def test_forward_to_the_client_is_not_duplicated_on_a_new_registration(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    s1 = FakeSender()
    process_mailbox(
        FakeReader([_notice_msg("k1")], {}, bodies={"k1": make_notice()}),
        **_followup_kwargs(store, s1),
    )
    assert len(s1.sent) == 2

    # the lawyer resends the SAME notice (REGISTER) in another message
    s2 = FakeSender()
    process_mailbox(
        FakeReader([_notice_msg("k2")], {}, bodies={"k2": make_notice()}),
        **_followup_kwargs(store, s2),
    )
    subjects = [e["subject"] for e in s2.sent]
    assert not any(s.startswith("ACORDO PACTUADO") for s in subjects), (
        "the client must not get the notice again"
    )
    assert any("registered" in s for s in subjects)  # the confirmation goes again


def test_correction_is_forwarded_to_the_client_with_a_mark(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    process_mailbox(
        FakeReader([_notice_msg("k1")], {}, bodies={"k1": make_notice()}),
        **_followup_kwargs(store, FakeSender()),
    )

    s2 = FakeSender()
    correction = make_notice(OPERATION="OPERATION: CORRECTION")
    process_mailbox(
        FakeReader([_notice_msg("k2")], {}, bodies={"k2": correction}),
        **_followup_kwargs(store, s2),
    )
    forward = s2.sent[0]
    assert forward["subject"].startswith("ACORDO PACTUADO (CORREÇÃO)")


def test_dry_run_does_not_consume_the_real_forward(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    sender = FakeSender(dry_run=True)
    process_mailbox(
        FakeReader([_notice_msg()], {}, bodies={"k1": make_notice()}),
        **_followup_kwargs(store, sender),
    )
    assert len(sender.sent) == 2  # simulated the forward + the confirmation
    assert not store.notice_sent(CASE_NOTICE)


def test_confirmation_failure_retries_without_duplicating_the_client(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    # 1st sweep: the forward (1st send) ok, the confirmation (2nd send) fails
    s1 = FakeSender(fail_at=2)
    r1 = process_mailbox(
        FakeReader([_notice_msg()], {}, bodies={"k1": make_notice()}), **_followup_kwargs(store, s1)
    )
    assert len(r1.errors) == 1
    assert len(s1.sent) == 1  # the client got it
    assert store.notice_sent(CASE_NOTICE)
    assert not store.message_processed("k1")  # will try again

    # 2nd sweep: does not forward to the client (the flag), the confirmation goes out
    s2 = FakeSender()
    r2 = process_mailbox(
        FakeReader([_notice_msg()], {}, bodies={"k1": make_notice()}), **_followup_kwargs(store, s2)
    )
    assert r2.notices == 1
    subjects = [e["subject"] for e in s2.sent]
    assert not any(s.startswith("ACORDO PACTUADO") for s in subjects)
    assert any("registered" in s for s in subjects)
    assert store.message_processed("k1")


def test_invalid_notice_warns_the_sender(tmp_path):
    body = "=== AUTOMATION-DATA v1 ===\nOPERATION: REGISTER\n=== END AUTOMATION-DATA ==="
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    sender = FakeSender()
    process_mailbox(
        FakeReader([_notice_msg()], {}, bodies={"k1": body}), **_followup_kwargs(store, sender)
    )
    (warning,) = sender.sent
    assert warning["to"] == ["lawyer@lawfirm.example"]
    assert "not processed" in warning["subject"]


def test_copy_of_an_own_send_is_ignored(tmp_path):
    # a reminder/notice sent with the mailbox itself in Cc comes back to the inbox: it
    # must not become a "check" item nor a reply
    store, extracted = _active_store(tmp_path)
    copy = MailboxMessage(
        id="cp1",
        subject=f"LEMBRETE DE PAGAMENTO DA 1ª PARCELA DE ACORDO - {extracted.case_number} - A X B",
        sender="automation@lawfirm.example",
        received_at="2026-08-31T08:00:00Z",
    )
    reader = FakeReader([copy], {})
    reader.mailbox = "automation@lawfirm.example"

    summary = process_mailbox(reader, **_notice_kwargs(store))

    assert summary.own_copies == 1
    assert summary.to_check == []
    assert summary.closed == []
    assert store.find(extracted.case_number).status == "active"
    assert store.message_processed("cp1")


def _settings_without(tmp_path, **over) -> Settings:
    data = dict(
        _env_file=None,
        history_db=str(tmp_path / "data.sqlite"),
        ingest_mailbox="automation@lawfirm.example",
        anthropic_api_key="",
        ms_tenant_id="",
        ms_client_id="",
        ms_client_secret="",
        client_directory_path="",
    )
    data.update(over)
    return Settings(**data)


def test_ingestion_is_skipped_without_a_complete_configuration(tmp_path):
    summary = Summary()
    _ingest_mailbox(_settings_without(tmp_path), summary)

    assert summary.ingested == 0
    assert summary.ingest_failures == 0
    assert summary.errors == []


def test_ingestion_is_off_without_a_mailbox(tmp_path):
    summary = Summary()
    _ingest_mailbox(_settings_without(tmp_path, ingest_mailbox=""), summary)

    assert summary.ingested == 0


# ------------------- guards against collecting from someone who paid -----------


def _reply_msg(case, mid="g1"):
    return MailboxMessage(
        id=mid,
        subject=f"RE: ACORDO PACTUADO - {case} - A X B",
        sender="finance@example.com",
        received_at="2026-09-21T18:54:00Z",
        has_attachments=True,
    )


def test_a_failed_reply_marks_the_case_without_collection_today(tmp_path):
    store, extracted = _active_store(tmp_path)
    attachments = {"g1": [Attachment(name="receipt.pdf", content=b"%PDF fake")]}

    summary = process_mailbox(
        FakeReader([_reply_msg(extracted.case_number)], attachments),
        **_kwargs(
            store,
            extractor=FailingExtractor(),
            receipt_reader=FixedReader(RuntimeError("API down")),
        ),
    )

    assert summary.failed_cases == {extracted.case_number}
    assert not store.message_processed("g1")  # retried tomorrow


def test_receipt_without_a_matching_installment_holds_in_the_store(tmp_path):
    store, extracted = _active_store(tmp_path)
    attachments = {"g1": [Attachment(name="receipt.pdf", content=b"%PDF fake")]}

    summary = process_mailbox(
        FakeReader([_reply_msg(extracted.case_number)], attachments),
        **_kwargs(
            store, extractor=FailingExtractor(), receipt_reader=FixedReader(receipt_ok("999999.00"))
        ),
    )

    assert len(summary.held) == 1 and extracted.case_number in summary.held[0]
    assert [c for c, _, _ in store.holds()] == [extracted.case_number]
    assert store.message_processed("g1")  # not reprocessed: waits for a human


# ------------------------ the notice opens the agreement's conversation -----------


class TrackedSender(FakeSender):
    def send_tracked(self, *, to, subject, html, cc=None, inline_images=None):
        self.send(to=to, subject=subject, html=html, cc=cc, inline_images=inline_images)
        return "conv-notice"


def test_tracked_notice_stores_the_agreements_conversation(tmp_path):
    store = AgreementStore(str(tmp_path / "data.sqlite"))
    sender = TrackedSender()
    reader = FakeReader([_notice_msg()], {}, bodies={"k1": make_notice()})

    summary = process_mailbox(reader, **_followup_kwargs(store, sender))

    assert summary.notices == 1
    assert store.without_thread() == []
    assert store.installments_for_reminders()[0].agreement.thread_id == "conv-notice"
