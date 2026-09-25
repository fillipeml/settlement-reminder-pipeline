"""Contract shared by every data source."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from settlement_reminders.models import Agreement


class DataSource(ABC):
    """A source of agreements: reads its origin (a spreadsheet, a SharePoint list) and
    returns agreements already normalised as `Agreement` objects."""

    name: str = "unknown"

    @abstractmethod
    def fetch_agreements(self) -> Iterable[Agreement]:
        raise NotImplementedError
