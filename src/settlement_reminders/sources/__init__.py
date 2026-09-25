"""Pluggable layer of agreement data sources."""

from __future__ import annotations

from settlement_reminders.config import Settings
from settlement_reminders.sources.base import DataSource
from settlement_reminders.sources.excel import ExcelSource


def build_sources(settings: Settings) -> list[DataSource]:
    """Instantiates the active sources according to `settings.sources`."""
    sources: list[DataSource] = []
    for name in settings.sources:
        if name == "notices":
            # agreements registered from notices live in the AgreementStore (SQLite) and
            # are handled directly by the orchestrator (run._collect_installments)
            continue
        if name == "excel":
            sources.append(ExcelSource(settings.excel_path))
        elif name == "sharepoint":
            from settlement_reminders.graph import GraphClient
            from settlement_reminders.sources.sharepoint import SharePointSource

            client = GraphClient(
                settings.ms_tenant_id, settings.ms_client_id, settings.ms_client_secret
            )
            sources.append(
                SharePointSource(client, settings.sharepoint_site, settings.sharepoint_list)
            )
        else:
            raise ValueError(f"Unknown source in SOURCES: {name!r}")
    return sources


__all__ = ["DataSource", "ExcelSource", "build_sources"]
