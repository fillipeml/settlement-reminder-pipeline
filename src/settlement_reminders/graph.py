"""Shared Microsoft Graph client (app-only authentication).

Used by the SharePoint source (list reading), the mailbox reader and the e-mail sender.
Client-credentials flow (MSAL), no interactive login, which is what "runs without an
operator" requires.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import msal
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPE = ["https://graph.microsoft.com/.default"]


class GraphError(RuntimeError):
    """A Microsoft Graph call failed."""


class GraphClient:
    """Minimal wrapper over Microsoft Graph with app-only auth."""

    def __init__(
        self, tenant_id: str, client_id: str, client_secret: str, *, timeout: float = 30.0
    ) -> None:
        if not (tenant_id and client_id and client_secret):
            raise GraphError(
                "Microsoft Graph credentials missing (MS_TENANT_ID/MS_CLIENT_ID/MS_CLIENT_SECRET)."
            )
        self._app = msal.ConfidentialClientApplication(
            client_id=client_id,
            client_credential=client_secret,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
        )
        self._client = httpx.Client(timeout=timeout)

    def _token(self) -> str:
        result = self._app.acquire_token_silent(_SCOPE, account=None)
        if not result:
            result = self._app.acquire_token_for_client(scopes=_SCOPE)
        if "access_token" not in result:
            raise GraphError(f"Failed to obtain a token: {result.get('error_description', result)}")
        return result["access_token"]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def get(self, path_or_url: str, params: dict | None = None) -> dict[str, Any]:
        url = path_or_url if path_or_url.startswith("http") else f"{GRAPH_BASE}{path_or_url}"
        resp = self._client.get(url, headers=self._headers(), params=params)
        if resp.status_code >= 400:
            raise GraphError(f"GET {url} -> {resp.status_code}: {resp.text}")
        return resp.json()

    def get_all(self, path: str, params: dict | None = None) -> list[dict[str, Any]]:
        """GET with automatic pagination (@odata.nextLink)."""
        items: list[dict[str, Any]] = []
        data = self.get(path, params=params)
        items.extend(data.get("value", []))
        while next_link := data.get("@odata.nextLink"):
            data = self.get(next_link)
            items.extend(data.get("value", []))
        return items

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def post(self, path: str, json: dict | None = None) -> httpx.Response:
        url = f"{GRAPH_BASE}{path}"
        resp = self._client.post(url, headers=self._headers(), json=json)
        if resp.status_code >= 400:
            raise GraphError(f"POST {url} -> {resp.status_code}: {resp.text}")
        return resp

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def patch(self, path: str, json: dict) -> httpx.Response:
        url = f"{GRAPH_BASE}{path}"
        resp = self._client.patch(url, headers=self._headers(), json=json)
        if resp.status_code >= 400:
            raise GraphError(f"PATCH {url} -> {resp.status_code}: {resp.text}")
        return resp

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GraphClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
