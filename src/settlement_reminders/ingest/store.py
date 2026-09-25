"""Persistence of the registered agreements (SQLite).

A notice arrives once; the reminders fire for months. This store keeps the
`ExtractedAgreement` (the whole JSON, auditable), the matched recipient and the agreement's
state:

- active ..... a trustworthy extraction + a recipient -> generates reminders
- pending .... some issue (a doubt of the extraction, no e-mail, an uncertain match) ->
               generates NO reminder; appears in the exceptions digest
- closed ..... the client paid everything, or a manual closing -> stops everything

Uses the same SQLite file as the send history: a single artefact to persist in production.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from settlement_reminders.ingest.convert import installments_from_extracted
from settlement_reminders.ingest.schema import ExtractedAgreement
from settlement_reminders.models import Installment

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agreements (
    case_number      TEXT PRIMARY KEY,
    data             TEXT NOT NULL,             -- JSON of the ExtractedAgreement
    client_emails    TEXT NOT NULL DEFAULT '',  -- CSV of the matched e-mails
    lawyer_email     TEXT NOT NULL DEFAULT '',  -- gets the confirmation and the escalation
    cc_extra         TEXT NOT NULL DEFAULT '',  -- extra copies indicated by the lawyer
    match_status     TEXT NOT NULL,             -- ok | no_email | not_found | low_confidence | notice | manual
    match_info       TEXT,
    status           TEXT NOT NULL,             -- active | pending | closed
    issues           TEXT NOT NULL DEFAULT '[]',-- JSON list[str] (reasons)
    origin           TEXT,                      -- source file/message
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    closed_at        TEXT,
    closed_reason    TEXT,
    notice_sent_at   TEXT,                      -- the notice forwarded to the client
    thread_id        TEXT NOT NULL DEFAULT ''   -- conversationId of the notice's thread
);

CREATE TABLE IF NOT EXISTS settlements (
    external_id TEXT PRIMARY KEY,               -- paid installment (e.g. case#O1P2)
    case_number TEXT NOT NULL,
    reason      TEXT NOT NULL,                  -- receipt | manual
    detail      TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS holds (
    case_number TEXT PRIMARY KEY,               -- collection and escalation ON HOLD until a human releases
    reason      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_messages (
    message_id   TEXT PRIMARY KEY,              -- id of the message in Graph
    subject      TEXT,
    sender       TEXT,
    received_at  TEXT,
    processed_at TEXT NOT NULL,
    result       TEXT NOT NULL                  -- summary of what was done
);
"""

# additive migrations for databases created by earlier versions: (column, definition)
_AGREEMENT_COLUMNS = (
    ("lawyer_email", "TEXT NOT NULL DEFAULT ''"),
    ("cc_extra", "TEXT NOT NULL DEFAULT ''"),
    ("notice_sent_at", "TEXT"),
    ("thread_id", "TEXT NOT NULL DEFAULT ''"),
)


@dataclass
class AgreementRecord:
    """A summarised view of a persisted agreement (for listings and alerts)."""

    case_number: str
    status: str
    match_status: str
    client_emails: list[str]
    issues: list[str]
    origin: str
    updated_at: str
    closed_reason: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _split(csv: str | None) -> list[str]:
    return [e for e in (csv or "").split(", ") if e]


class AgreementStore:
    """Access to the database of registered agreements."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        path = Path(db_path)
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)
            conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(agreements)")}
        for name, definition in _AGREEMENT_COLUMNS:
            if name not in columns:
                conn.execute(f"ALTER TABLE agreements ADD COLUMN {name} {definition}")
                logger.info("Store: column %s added (migration)", name)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def upsert(
        self,
        extracted: ExtractedAgreement,
        *,
        client_emails: list[str],
        match_status: str,
        match_info: str,
        status: str,
        issues: list[str],
        origin: str = "",
        lawyer_email: str = "",
        cc_extra: list[str] | None = None,
    ) -> str:
        """Inserts/updates an agreement; returns the final persisted status.

        An agreement already CLOSED is never reopened by reprocessing (closing is an
        operational fact, not a datum of the draft).
        """
        now = _now()
        with closing(self._connect()) as conn:
            current = conn.execute(
                "SELECT status, created_at FROM agreements WHERE case_number = ?",
                (extracted.case_number,),
            ).fetchone()

            if current and current["status"] == "closed":
                logger.info(
                    "Agreement %s already closed; reprocessing ignored.", extracted.case_number
                )
                return "closed"

            conn.execute(
                """
                INSERT INTO agreements
                    (case_number, data, client_emails, lawyer_email, cc_extra, match_status, match_info,
                     status, issues, origin, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (case_number) DO UPDATE SET
                    data = excluded.data,
                    client_emails = excluded.client_emails,
                    lawyer_email = excluded.lawyer_email,
                    cc_extra = excluded.cc_extra,
                    match_status = excluded.match_status,
                    match_info = excluded.match_info,
                    status = excluded.status,
                    issues = excluded.issues,
                    origin = excluded.origin,
                    updated_at = excluded.updated_at
                """,
                (
                    extracted.case_number,
                    extracted.model_dump_json(),
                    ", ".join(client_emails),
                    lawyer_email,
                    ", ".join(cc_extra or []),
                    match_status,
                    match_info,
                    status,
                    json.dumps(issues, ensure_ascii=False),
                    origin,
                    current["created_at"] if current else now,
                    now,
                ),
            )
            conn.commit()
        return status

    def close(self, case_number: str, reason: str) -> bool:
        """Closes an agreement (stops generating reminders). True when it existed."""
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "UPDATE agreements SET status = 'closed', closed_at = ?, closed_reason = ?, updated_at = ? WHERE case_number = ?",
                (_now(), reason, _now(), case_number),
            )
            conn.commit()
            closed = cur.rowcount > 0
        if closed:
            self.release_collection(case_number)
        return closed

    def reopen(self, case_number: str) -> bool:
        """Reopens an agreement closed by mistake. True when it existed."""
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "UPDATE agreements SET status = 'active', closed_at = NULL, closed_reason = NULL,"
                " updated_at = ? WHERE case_number = ?",
                (_now(), case_number),
            )
            conn.commit()
            return cur.rowcount > 0

    # ---- settlements per installment (a receipt or a manual settlement) ----------------

    def settle_installment(
        self, external_id: str, case_number: str, reason: str, detail: str = ""
    ) -> bool:
        """Records the settlement of ONE installment. False when it was already settled."""
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO settlements (external_id, case_number, reason, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                (external_id, case_number, reason, detail, _now()),
            )
            conn.commit()
            settled = bool(cur.rowcount)
        if settled:
            logger.info("Installment %s settled (%s): %s", external_id, reason, detail)
            # a settlement is the reconciliation the hold was waiting for
            self.release_collection(case_number)
        return settled

    def unsettle(self, external_id: str) -> bool:
        """Undoes a settlement made by mistake. True when it existed."""
        with closing(self._connect()) as conn:
            cur = conn.execute("DELETE FROM settlements WHERE external_id = ?", (external_id,))
            conn.commit()
            return cur.rowcount > 0

    def settled_installments(self, case_number: str) -> set[str]:
        """external_id of every settled installment of this case."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT external_id FROM settlements WHERE case_number = ?", (case_number,)
            ).fetchall()
        return {r["external_id"] for r in rows}

    # ---- collection hold: a client attachment that did not settle -----------------------

    def hold_collection(self, case_number: str, reason: str) -> bool:
        """Suspends collection and escalation of the case until a human releases it.

        Born after two clients were collected the day after paying: the receipt arrived,
        was not read, and the collection went out. Rule since then: a client's reply with an
        attachment that neither settled an installment nor was recognised as a
        "non-receipt" locks the collection; in doubt, do not collect. Released by a
        settlement, by closing or by the CLI (`release-hold`). False when already on hold.
        """
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO holds (case_number, reason, created_at) VALUES (?, ?, ?)",
                (case_number, reason, _now()),
            )
            conn.commit()
            if cur.rowcount:
                logger.warning("Collection ON HOLD for %s: %s", case_number, reason)
            return bool(cur.rowcount)

    def release_collection(self, case_number: str) -> bool:
        """Removes the hold (reconciled by a human or by a settlement)."""
        with closing(self._connect()) as conn:
            cur = conn.execute("DELETE FROM holds WHERE case_number = ?", (case_number,))
            conn.commit()
            if cur.rowcount:
                logger.info("Collection released for %s", case_number)
            return cur.rowcount > 0

    def holds(self) -> list[tuple[str, str, str]]:
        """(case_number, reason, created_at) of every hold in force."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT case_number, reason, created_at FROM holds ORDER BY created_at"
            ).fetchall()
        return [(r["case_number"], r["reason"], r["created_at"]) for r in rows]

    def installments_of(self, case_number: str) -> list[Installment]:
        """Installments of ONE active agreement (empty when missing/pending/closed)."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT data, client_emails, lawyer_email, cc_extra FROM agreements WHERE case_number = ? AND status = 'active'",
                (case_number,),
            ).fetchone()
        if row is None:
            return []
        extracted = ExtractedAgreement.model_validate_json(row["data"])
        return installments_from_extracted(
            extracted,
            _split(row["client_emails"]),
            lawyer_email=row["lawyer_email"] or None,
            cc_emails=_split(row["cc_extra"]),
        )

    def set_emails(
        self, case_number: str, emails: list[str], *, reason: str = "", lawyer: str | None = None
    ) -> str | None:
        """OPERATOR override: redefines the agreement's recipients.

        Used for acceptance tests (pointing the cycle at the lawyer before the client) and
        to activate agreements pending on the recipient. Removes the recipient issues; when
        nothing else is pending the agreement becomes ACTIVE. Returns the final status, or
        None when the case does not exist / is closed.
        """
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT status, issues FROM agreements WHERE case_number = ?", (case_number,)
            ).fetchone()
            if row is None or row["status"] == "closed":
                return None
            issues = [
                i
                for i in json.loads(row["issues"] or "[]")
                if "recipient" not in i.lower() and "e-mail" not in i.lower()
            ]
            status = "active" if not issues else "pending"
            if lawyer is not None:
                conn.execute(
                    "UPDATE agreements SET lawyer_email = ? WHERE case_number = ?",
                    (lawyer, case_number),
                )
            conn.execute(
                "UPDATE agreements SET client_emails = ?, match_status = 'manual', match_info = ?,"
                " status = ?, issues = ?, updated_at = ?"
                " WHERE case_number = ?",
                (
                    ", ".join(emails),
                    f"operator override: {reason}" if reason else "operator override",
                    status,
                    json.dumps(issues, ensure_ascii=False),
                    _now(),
                    case_number,
                ),
            )
            conn.commit()
        logger.info(
            "Recipient override on %s -> %s (status=%s)%s",
            case_number,
            ", ".join(emails),
            status,
            f" [{reason}]" if reason else "",
        )
        return status

    def notice_sent(self, case_number: str) -> bool:
        """True when this agreement's notice was REALLY forwarded to the client."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT notice_sent_at FROM agreements WHERE case_number = ?", (case_number,)
            ).fetchone()
        return bool(row and row["notice_sent_at"])

    def set_thread(self, case_number: str, conversation_id: str) -> bool:
        """Stores the notice's conversation (reminders/collections reply in it)."""
        if not conversation_id:
            return False
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "UPDATE agreements SET thread_id = ?, updated_at = ? WHERE case_number = ?",
                (conversation_id, _now(), case_number),
            )
            conn.commit()
            return cur.rowcount > 0

    def without_thread(self) -> list[str]:
        """ACTIVE cases that do not have the notice's conversation linked yet."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT case_number FROM agreements WHERE status = 'active' AND thread_id = '' ORDER BY case_number"
            ).fetchall()
        return [r["case_number"] for r in rows]

    def mark_notice_sent(self, case_number: str) -> None:
        """Records the real forwarding of the notice to the client (idempotency)."""
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE agreements SET notice_sent_at = ? WHERE case_number = ?",
                (_now(), case_number),
            )
            conn.commit()

    def find(self, case_number: str) -> AgreementRecord | None:
        """The summarised record of an agreement by case number."""
        records = [r for r in self.list_agreements() if r.case_number == case_number]
        return records[0] if records else None

    def list_agreements(self, status: str | None = None) -> list[AgreementRecord]:
        sql = "SELECT case_number, status, match_status, client_emails, issues, origin, updated_at, closed_reason FROM agreements"
        params: tuple = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        with closing(self._connect()) as conn:
            rows = conn.execute(sql + " ORDER BY updated_at DESC", params).fetchall()
        return [
            AgreementRecord(
                case_number=r["case_number"],
                status=r["status"],
                match_status=r["match_status"],
                client_emails=_split(r["client_emails"]),
                issues=json.loads(r["issues"] or "[]"),
                origin=r["origin"] or "",
                updated_at=r["updated_at"],
                closed_reason=r["closed_reason"],
            )
            for r in rows
        ]

    def raw_agreement(self, case_number: str) -> sqlite3.Row | None:
        """The full row (used by the admin notice send and the audit)."""
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT * FROM agreements WHERE case_number = ?", (case_number,)
            ).fetchone()

    # ---- log of the mailbox messages (idempotency without Mail.ReadWrite: the mailbox is
    # ---- never modified; the control is here) -----------------------------------------

    def message_processed(self, message_id: str) -> bool:
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "SELECT 1 FROM processed_messages WHERE message_id = ? LIMIT 1", (message_id,)
            )
            return cur.fetchone() is not None

    def record_message(
        self,
        message_id: str,
        *,
        subject: str = "",
        sender: str = "",
        received_at: str = "",
        result: str = "",
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO processed_messages (message_id, subject, sender, received_at, processed_at, result)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, subject, sender, received_at, _now(), result),
            )
            conn.commit()

    def installments_for_reminders(self) -> list[Installment]:
        """Expands every ACTIVE agreement into installments ready for the rules."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT case_number, data, client_emails, lawyer_email, cc_extra, thread_id"
                " FROM agreements WHERE status = 'active'"
            ).fetchall()

        installments: list[Installment] = []
        for r in rows:
            extracted = ExtractedAgreement.model_validate_json(r["data"])
            installments.extend(
                installments_from_extracted(
                    extracted,
                    _split(r["client_emails"]),
                    lawyer_email=r["lawyer_email"] or None,
                    cc_emails=_split(r["cc_extra"]),
                    thread_id=r["thread_id"] or "",
                )
            )
        logger.info(
            "Store: %d active agreement(s) -> %d installment(s)", len(rows), len(installments)
        )
        return installments
