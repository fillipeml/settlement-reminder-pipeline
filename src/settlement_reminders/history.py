"""Send history in SQLite + idempotency.

Guarantees the same installment never gets a duplicate reminder and keeps an auditable
record of what went out.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sends (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id   TEXT NOT NULL,
    due_date      TEXT NOT NULL,
    case_number   TEXT,
    client_email  TEXT,
    source        TEXT,
    status        TEXT NOT NULL,          -- sent | failed | simulated
    error         TEXT,
    sent_at       TEXT NOT NULL,          -- ISO timestamp (UTC)
    run_day       TEXT,                    -- the routine's 'today' (pinned in tests and demos)
    UNIQUE (external_id, due_date, status)
);
"""


def now_utc() -> datetime:
    """Wrapped so tests can pin the clock."""
    return datetime.now(UTC)


class SendHistory:
    """Access to the send history database."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        path = Path(db_path)
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sends)")}
            if "run_day" not in columns:  # databases created before the column existed
                conn.execute("ALTER TABLE sends ADD COLUMN run_day TEXT")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def already_sent(self, external_id: str, due: date, *, count_simulated: bool = True) -> bool:
        """True when this installment was already handled in this mode.

        `count_simulated=True` (DRY_RUN): a previous simulation counts, so the same
        installment is not re-simulated every day. `count_simulated=False` (real send):
        ONLY real sends count; a simulation made in shadow mode never consumes the real send
        after go-live.
        """
        statuses = ("sent", "simulated") if count_simulated else ("sent",)
        placeholders = ", ".join("?" for _ in statuses)
        with closing(self._connect()) as conn:
            cur = conn.execute(
                f"SELECT 1 FROM sends WHERE external_id = ? AND due_date = ?"
                f" AND status IN ({placeholders}) LIMIT 1",
                (external_id, due.isoformat(), *statuses),
            )
            return cur.fetchone() is not None

    def collected_today(
        self, external_id_prefix: str, today: date, *, count_simulated: bool = True
    ) -> bool:
        """True when a collection of this installment/agreement ALREADY went out today.

        Guarantees at most one collection per day even when the routine runs more than
        once (or catches up late attempts, which are replayed one per day). Compares the
        run day recorded with the send, so a pinned date (tests, demo walkthroughs) is
        not confused with the wall clock.
        """
        statuses = ("sent", "simulated") if count_simulated else ("sent",)
        placeholders = ", ".join("?" for _ in statuses)
        with closing(self._connect()) as conn:
            cur = conn.execute(
                f"SELECT 1 FROM sends WHERE external_id LIKE ? AND run_day = ?"
                f" AND status IN ({placeholders}) LIMIT 1",
                (external_id_prefix + "%", today.isoformat(), *statuses),
            )
            return cur.fetchone() is not None

    def record(
        self,
        *,
        external_id: str,
        due: date,
        case_number: str,
        client_email: str,
        source: str,
        status: str,
        error: str | None = None,
        run_day: date | None = None,
    ) -> None:
        """Records a send (sent | simulated | failed) on the routine's `run_day` (default: now)."""
        stamp = now_utc()
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sends"
                " (external_id, due_date, case_number, client_email, source, status, error,"
                " sent_at, run_day)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    external_id,
                    due.isoformat(),
                    case_number,
                    client_email,
                    source,
                    status,
                    error,
                    stamp.isoformat(),
                    (run_day or stamp.date()).isoformat(),
                ),
            )
            conn.commit()

    def real_sends(self, since: str = "1970-01-01") -> list[sqlite3.Row]:
        """Real sends and failures after `since` (ISO), oldest first: the audit's input."""
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT * FROM sends WHERE status IN ('sent', 'failed') AND sent_at > ?"
                " ORDER BY sent_at",
                (since,),
            ).fetchall()
