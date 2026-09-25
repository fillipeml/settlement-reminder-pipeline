"""Application settings, loaded from environment variables (and an optional `.env`).

`config.py` and `factory.py` are the only modules that read `DEMO_MODE`; business modules
receive already-built collaborators.
"""

from __future__ import annotations

import unicodedata
from datetime import date
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEMO_REFERENCE_DATE = date(2026, 9, 17)  # a Thursday; the fixtures are dated around it


def normalise(text: str) -> str:
    """Lower case, no accents, collapsed spaces (to compare names)."""
    without_accents = "".join(
        ch for ch in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(ch)
    )
    return " ".join(without_accents.lower().split())


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    """Settings read from the environment. See `.env.example` for the reference."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # -- cycle ---------------------------------------------------------------------------
    days_before_due: int = Field(default=3, ge=0)
    # Post-due collection cycle: an installment overdue without a settlement is collected
    # every N BUSINESS days, up to the cap of attempts; when the schedule is exhausted the
    # installment ESCALATES to the lawyer and the automation stops collecting it. Cap 0 =
    # collection off (preventive reminders only).
    collection_interval_business_days: int = Field(default=2, ge=1)
    collection_max_attempts: int = Field(default=5, ge=0)
    # Recovery window: if the run of the target day was missed (machine off), the reminder
    # still goes out up to N calendar days later, on a business day and never after the due
    # date. 0 = off.
    catchup_days: int = Field(default=2, ge=0)
    # Reminders and collections go out as REPLIES in the conversation opened by the
    # agreement's notice (one thread per agreement). Requires Mail.ReadWrite on the Graph
    # app; without the permission the send falls back to a new message by itself.
    thread_replies: bool = True
    timezone: str = "America/Sao_Paulo"
    # Public holidays: national + the state subdivision of the firm (ISO code).
    holiday_subdivision: str = "GO"
    dry_run: bool = True
    demo_mode: bool = False
    # Pins "today" (ISO date); the demo uses it so the fixtures line up with the calendar.
    reference_date: date | None = None

    # -- sources -------------------------------------------------------------------------
    # active sources, comma separated: notices,excel,sharepoint
    #   notices    = agreements registered from notice e-mails (the store)
    #   excel      = a local spreadsheet (tests, small teams)
    #   sharepoint = a SharePoint list through Graph
    sources: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["excel"])
    excel_path: str = "fixtures/agreements.xlsx"

    # -- Microsoft Graph (shared by SharePoint and e-mail) --------------------------------
    ms_tenant_id: str = ""
    ms_client_id: str = ""
    ms_client_secret: str = ""
    ms_sender: str = ""
    firm_name: str = "Example Law Firm"
    sender_name: str = ""
    # Signature of the automation's e-mails to the client: "Atenciosamente, <department>";
    # never a person's name.
    signature_department: str = "Controladoria Jurídica"

    sharepoint_site: str = ""
    sharepoint_list: str = ""

    # -- model (receipt reading; legacy draft extraction and recipient matching) ---------
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    # client directory (name -> e-mails), a spreadsheet exported from the case system
    client_directory_path: str = ""

    # -- ingestion mailbox ----------------------------------------------------------------
    ingest_mailbox: str = ""
    ingest_lookback_days: int = Field(default=7, ge=1)
    # Only these sender suffixes (e.g. "@lawfirm.example") may submit NOTICES and DRAFTS;
    # replies from clients are not affected. Empty = accept from anyone.
    ingest_allowed_senders: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # The firm's e-mail domain: internal addresses are never treated as client addresses
    # and lawyers may forward a client's receipt.
    internal_domain: str = "lawfirm.example"

    # -- copies and alerts ----------------------------------------------------------------
    alert_emails: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Controllership (awareness, not approval): Cc of everything sent to the client, gets
    # the registration confirmations and the escalations.
    controllership_emails: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Extra Cc of the notices and the preventive reminders ONLY (not of collections).
    cc_notices: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Extra copies BY PAYER, in reminders and collections of every agreement whose payer
    # contains the key (substring, accent and case insensitive), including future ones.
    # Format: "key: email1, email2; other key: email3".
    cc_by_payer: Annotated[dict[str, list[str]], NoDecode] = Field(default_factory=dict)

    history_db: str = "data/history.sqlite"

    @field_validator(
        "sources",
        "alert_emails",
        "ingest_allowed_senders",
        "controllership_emails",
        "cc_notices",
        mode="before",
    )
    @classmethod
    def _csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("cc_by_payer", mode="before")
    @classmethod
    def _payer_map(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        mapping: dict[str, list[str]] = {}
        for entry in value.split(";"):
            key, _, emails = entry.partition(":")
            target = normalise(key)
            addresses = [e.strip() for e in emails.split(",") if e.strip()]
            if target and addresses:
                mapping.setdefault(target, []).extend(addresses)
        return mapping

    @field_validator("reference_date", mode="before")
    @classmethod
    def _empty_date(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("demo_mode", "dry_run", "thread_replies", mode="before")
    @classmethod
    def _empty_bool(cls, value: object) -> object:
        return False if value == "" else value

    @field_validator("internal_domain", mode="before")
    @classmethod
    def _domain(cls, value: object) -> object:
        return value.strip().lstrip("@").lower() if isinstance(value, str) else value

    # -- helpers -------------------------------------------------------------------------
    @property
    def display_name(self) -> str:
        return self.sender_name or self.firm_name

    def is_internal(self, email: str) -> bool:
        return (email or "").lower().endswith(f"@{self.internal_domain}")

    def cc_for_payer(self, payer: str) -> list[str]:
        """Extra copies that apply to this agreement's payer (CC_BY_PAYER).

        Substring match on the payer's name: an agreement with several defendants matches
        the key of any of them, because all of them answer for the payment.
        """
        target = normalise(payer)
        out: list[str] = []
        for key, emails in self.cc_by_payer.items():
            if key and key in target:
                out.extend(e for e in emails if e not in out)
        return out

    def for_demo(self) -> Settings:
        """A copy with the demo defaults filled in wherever the field is empty."""
        defaults = {
            "history_db": ".demo/state.sqlite",
            "sources": ["notices", "excel"],
            "excel_path": "fixtures/agreements.xlsx",
            "client_directory_path": "fixtures/clients.xlsx",
            "ingest_mailbox": "automation@lawfirm.example",
            "ms_sender": "automation@lawfirm.example",
            "ingest_allowed_senders": ["@lawfirm.example"],
            "controllership_emails": ["controllership@lawfirm.example"],
            "alert_emails": ["operator@lawfirm.example"],
            "cc_notices": ["labour@lawfirm.example"],
            "reference_date": DEMO_REFERENCE_DATE,
        }
        update = {
            key: value
            for key, value in defaults.items()
            if key in self.model_fields_set
            and getattr(self, key) in ("", [], None)
            or key not in self.model_fields_set
        }
        update["demo_mode"] = True
        return self.model_copy(update=update)


def get_settings() -> Settings:
    settings = Settings()
    return settings.for_demo() if settings.demo_mode else settings
