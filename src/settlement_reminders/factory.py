"""Builds the collaborators from the settings. The only place, with `config.py`, that knows
about DEMO_MODE."""

from __future__ import annotations

from pathlib import Path

from settlement_reminders.config import Settings
from settlement_reminders.history import SendHistory
from settlement_reminders.run import Ingest, Summary, run_ingest


def build_sender(settings: Settings):
    if settings.demo_mode:
        from settlement_reminders.demo import OutboxSender

        outbox = Path(settings.history_db).parent / "outbox"
        return OutboxSender(outbox, sender=settings.ms_sender or "automation@lawfirm.example")

    from settlement_reminders.mailer import EmailSender

    graph = None
    if not settings.dry_run:
        from settlement_reminders.graph import GraphClient

        graph = GraphClient(settings.ms_tenant_id, settings.ms_client_id, settings.ms_client_secret)
    return EmailSender(graph, settings.ms_sender, settings.display_name, dry_run=settings.dry_run)


def build_history(settings: Settings) -> SendHistory:
    return SendHistory(settings.history_db)


def build_ingest(settings: Settings, sender) -> Ingest | None:
    """The demo's mailbox sweep over the fixtures; None = the real sweep from the settings."""
    if not settings.demo_mode:
        return None

    from settlement_reminders.demo import FixtureExtractor, FixtureMailbox, FixtureReceiptReader
    from settlement_reminders.ingest import AgreementStore, ExcelClientDirectory, RecipientMatcher

    contacts = ExcelClientDirectory(settings.client_directory_path).fetch_contacts()

    def ingest(summary: Summary) -> None:
        run_ingest(
            settings,
            summary,
            reader=FixtureMailbox(mailbox=settings.ingest_mailbox),
            extractor=FixtureExtractor(),
            matcher=RecipientMatcher(contacts),  # exact matches only: no key needed
            receipt_reader=FixtureReceiptReader(),
            store=AgreementStore(settings.history_db),
            sender=sender,
            today=None,
        )

    return ingest
