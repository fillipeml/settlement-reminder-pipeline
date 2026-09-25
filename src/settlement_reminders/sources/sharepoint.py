"""Data source: a SharePoint list (through Microsoft Graph).

Reads the items of a SharePoint list and normalises them into `Agreement`, reusing the
tolerant column mapping of the Excel reader.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from pydantic import ValidationError

from settlement_reminders.models import Agreement
from settlement_reminders.sources.base import DataSource
from settlement_reminders.sources.excel import map_columns, row_to_agreement

logger = logging.getLogger(__name__)

# SharePoint internal names encode characters as _x0020_ (space)
_SP_ENCODED = re.compile(r"_x([0-9A-Fa-f]{4})_")


def decode_internal_name(name: str) -> str:
    return _SP_ENCODED.sub(lambda m: chr(int(m.group(1), 16)), name)


class SharePointSource(DataSource):
    """Reads agreements from a SharePoint list."""

    name = "sharepoint"

    def __init__(self, client, site: str, list_name: str) -> None:
        if not site or not list_name:
            raise ValueError("SHAREPOINT_SITE and SHAREPOINT_LIST are required.")
        self.client = client
        self.site = site
        self.list_name = list_name

    def _resolve_site_id(self) -> str:
        return self.client.get(f"/sites/{self.site}")["id"]

    def _resolve_list_id(self, site_id: str) -> str:
        lists = self.client.get_all(f"/sites/{site_id}/lists", params={"$select": "id,displayName"})
        for lst in lists:
            if self.list_name in (lst.get("id"), lst.get("displayName")):
                return lst["id"]
        raise ValueError(
            f"List {self.list_name!r} not found. Available: {[x.get('displayName') for x in lists]}"
        )

    def fetch_agreements(self) -> Iterable[Agreement]:
        site_id = self._resolve_site_id()
        list_id = self._resolve_list_id(site_id)
        items = self.client.get_all(
            f"/sites/{site_id}/lists/{list_id}/items", params={"$expand": "fields", "$top": "200"}
        )
        logger.info("SharePoint: %d item(s) in list %r", len(items), self.list_name)

        agreements: list[Agreement] = []
        col_map: dict[str, str] | None = None
        for item in items:
            fields = item.get("fields", {})
            if not fields:
                continue
            if col_map is None:
                decoded = {decode_internal_name(k): k for k in fields}
                logical = map_columns(decoded.keys(), where="list")
                col_map = {field: decoded[name] for field, name in logical.items()}

            def get(field: str, _f=fields, _c=col_map):
                key = _c.get(field)
                return _f.get(key) if key else None

            try:
                agreement = row_to_agreement(get)
            except (ValidationError, ValueError) as exc:
                logger.warning("Item %s ignored: %s", item.get("id"), exc)
                continue
            if agreement.active:
                agreements.append(agreement)
        return agreements
