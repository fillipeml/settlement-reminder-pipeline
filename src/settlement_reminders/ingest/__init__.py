"""Ingestion pipeline: notices, drafts, receipts.

Main path: notices written by the `settlement-notice` skill arrive with an AUTOMATION-DATA
block in the body and already carry the client's e-mails; `parse_notice`/`process_notice`
register them without a model call.

Legacy path: a settlement draft (PDF) forwarded without the skill goes through the model
(`AgreementExtractor`), the recipient is matched against the client directory
(`RecipientMatcher`) and the agreement is persisted (`AgreementStore`).

Receipts: a client's reply is matched to the agreement by the case number in the subject,
its attachments are read by the model (`ReceiptReader`) and a deterministic match by amount
settles the installment.
"""

from settlement_reminders.ingest.convert import installments_from_extracted
from settlement_reminders.ingest.directory import (
    ClientContact,
    ClientDirectory,
    ExcelClientDirectory,
)
from settlement_reminders.ingest.extract import DEFAULT_MODEL, AgreementExtractor, ExtractionError
from settlement_reminders.ingest.matcher import MatchResult, RecipientMatcher
from settlement_reminders.ingest.notice import InvalidNoticeError, ParsedNotice, parse_notice
from settlement_reminders.ingest.pipeline import IngestResult, process_draft, process_notice
from settlement_reminders.ingest.receipt import ExtractedReceipt, ReceiptReader, match_installment
from settlement_reminders.ingest.schema import (
    ExtractedAgreement,
    ExtractedInstallment,
    PaymentObligation,
)
from settlement_reminders.ingest.store import AgreementRecord, AgreementStore

__all__ = [
    "AgreementExtractor",
    "AgreementRecord",
    "AgreementStore",
    "ClientContact",
    "ClientDirectory",
    "DEFAULT_MODEL",
    "ExcelClientDirectory",
    "ExtractedAgreement",
    "ExtractedInstallment",
    "ExtractedReceipt",
    "ExtractionError",
    "IngestResult",
    "MatchResult",
    "InvalidNoticeError",
    "ParsedNotice",
    "PaymentObligation",
    "ReceiptReader",
    "RecipientMatcher",
    "installments_from_extracted",
    "match_installment",
    "parse_notice",
    "process_draft",
    "process_notice",
]
