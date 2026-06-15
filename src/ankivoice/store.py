"""Durable SQLite job store + state machine (load-bearing). data-model.md.

The only datastore. All access is single-threaded (the asyncio event-loop thread); a lock guards the
atomic claim. WAL + busy_timeout for durability. ``requeue_in_progress`` makes a restart resume
pending work (FR-021); ``DELIVERED`` jobs are intentionally NOT requeued (they are already delivered —
re-running delivery would double-send; the worker cleans them at startup instead).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import Job, JobState

# Sentinel input_path used between enqueue and the upload being saved. A job with this path is NOT
# yet claimable (its input file does not exist), which closes the enqueue→download→claim race.
PENDING_INPUT = "pending"

# Non-terminal states = an "active" job for the one-job-per-user rule (FR-020).
# Cycle 002: PACKAGING removed (was never observable as a distinct step).
_ACTIVE = (
    JobState.QUEUED,
    JobState.SYNTHESIZING,
    JobState.UPLOADING,
    JobState.DELIVERED,
)
# States whose work must be rebuilt from the input file after a restart. A legacy 'packaging' string
# from a pre-002 DB is also treated as rebuildable (see requeue_in_progress).
_REBUILDABLE = (JobState.SYNTHESIZING, JobState.UPLOADING)
_LEGACY_REBUILDABLE = ("packaging",)
# Jobs "ahead in line" when computing a queue position: queued + the one currently synthesizing. A job
# that has finished synthesis and is uploading/delivering no longer blocks a queued job.
_AHEAD = (JobState.QUEUED, JobState.SYNTHESIZING)
# Terminal states eligible for the bounded history prune.
_TERMINAL = (JobState.CLEANED, JobState.FAILED)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    chat_id           INTEGER NOT NULL,
    input_path        TEXT NOT NULL,
    original_filename TEXT,
    state             TEXT NOT NULL,
    error_reason      TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    archive_sent      INTEGER NOT NULL DEFAULT 0,
    user_sent         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id);
"""

# Additive migration columns for databases created before cycle 002.
_MIGRATIONS = (
    ("archive_sent", "ALTER TABLE jobs ADD COLUMN archive_sent INTEGER NOT NULL DEFAULT 0"),
    ("user_sent", "ALTER TABLE jobs ADD COLUMN user_sent INTEGER NOT NULL DEFAULT 0"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        user_id=row["user_id"],
        chat_id=row["chat_id"],
        input_path=row["input_path"],
        original_filename=row["original_filename"],
        state=JobState(row["state"]),
        error_reason=row["error_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        archive_sent=bool(row["archive_sent"]),
        user_sent=bool(row["user_sent"]),
    )


class JobStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path), isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._lock = threading.Lock()

    def _migrate(self) -> None:
        """Additively add cycle-002 columns to a pre-002 database (idempotent)."""
        existing = {r["name"] for r in self._conn.execute("PRAGMA table_info(jobs)")}
        for column, ddl in _MIGRATIONS:
            if column not in existing:
                self._conn.execute(ddl)

    def close(self) -> None:
        self._conn.close()

    def has_active_job(self, user_id: int) -> bool:
        placeholders = ",".join("?" for _ in _ACTIVE)
        row = self._conn.execute(
            f"SELECT 1 FROM jobs WHERE user_id=? AND state IN ({placeholders}) LIMIT 1",
            (user_id, *[s.value for s in _ACTIVE]),
        ).fetchone()
        return row is not None

    def enqueue(
        self, *, user_id: int, chat_id: int, input_path: str, original_filename: str | None
    ) -> Job:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO jobs (user_id, chat_id, input_path, original_filename, state, "
                "error_reason, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (user_id, chat_id, input_path, original_filename, JobState.QUEUED.value, None, now, now),
            )
            job_id = cur.lastrowid
        return self.get(job_id)  # type: ignore[return-value]

    def enqueue_if_no_active(
        self, *, user_id: int, chat_id: int, input_path: str, original_filename: str | None
    ) -> Job | None:
        """Atomically reserve a slot: insert a QUEUED job IFF the user has no active job, else None.

        The active-check and the insert run inside one IMMEDIATE transaction so two near-simultaneous
        uploads from the same user can never both create an active job (FR-020, research D9).
        """
        now = _now()
        placeholders = ",".join("?" for _ in _ACTIVE)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                active = self._conn.execute(
                    f"SELECT 1 FROM jobs WHERE user_id=? AND state IN ({placeholders}) LIMIT 1",
                    (user_id, *[s.value for s in _ACTIVE]),
                ).fetchone()
                if active is not None:
                    self._conn.execute("ROLLBACK")
                    return None
                cur = self._conn.execute(
                    "INSERT INTO jobs (user_id, chat_id, input_path, original_filename, state, "
                    "error_reason, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (user_id, chat_id, input_path, original_filename, JobState.QUEUED.value, None, now, now),
                )
                job_id = cur.lastrowid
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return self.get(job_id)  # type: ignore[return-value]

    def queue_position(self, job_id: int) -> int:
        placeholders = ",".join("?" for _ in _AHEAD)
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM jobs WHERE id <= ? AND state IN ({placeholders})",
            (job_id, *[s.value for s in _AHEAD]),
        ).fetchone()
        return int(row["n"])

    def claim_next(self) -> Job | None:
        with self._lock:
            # Only claim QUEUED jobs whose input file has actually been saved (input_path is no longer
            # the PENDING_INPUT sentinel) — never start a job mid-download (FR-017).
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE state=? AND input_path != ? ORDER BY id ASC LIMIT 1",
                (JobState.QUEUED.value, PENDING_INPUT),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE jobs SET state=?, updated_at=? WHERE id=?",
                (JobState.SYNTHESIZING.value, _now(), row["id"]),
            )
            return self.get(row["id"])

    def set_input_path(self, job_id: int, input_path: str) -> None:
        """Record the saved upload path once the job dir (which is named by job id) exists."""
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET input_path=?, updated_at=? WHERE id=?",
                (input_path, _now(), job_id),
            )

    def set_state(self, job_id: int, state: JobState, *, error_reason: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET state=?, error_reason=?, updated_at=? WHERE id=?",
                (state.value, error_reason, _now(), job_id),
            )

    def set_delivery_flag(
        self, job_id: int, *, archive: bool | None = None, user: bool | None = None
    ) -> None:
        """Record that a delivery copy has been sent, so deliver() is idempotent across restarts (D8)."""
        sets, params = [], []
        if archive is not None:
            sets.append("archive_sent=?")
            params.append(1 if archive else 0)
        if user is not None:
            sets.append("user_sent=?")
            params.append(1 if user else 0)
        if not sets:
            return
        params.extend([_now(), job_id])
        with self._lock:
            self._conn.execute(
                f"UPDATE jobs SET {', '.join(sets)}, updated_at=? WHERE id=?", tuple(params)
            )

    def get(self, job_id: int) -> Job | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def list_active(self) -> list[Job]:
        placeholders = ",".join("?" for _ in _ACTIVE)
        rows = self._conn.execute(
            f"SELECT * FROM jobs WHERE state IN ({placeholders}) ORDER BY id ASC",
            tuple(s.value for s in _ACTIVE),
        ).fetchall()
        return [_row_to_job(r) for r in rows]

    def list_in_state(self, state: JobState) -> list[Job]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE state=? ORDER BY id ASC", (state.value,)
        ).fetchall()
        return [_row_to_job(r) for r in rows]

    def list_abandoned_uploads(self) -> list[Job]:
        """QUEUED jobs whose input never finished saving (upload interrupted, e.g. by a restart)."""
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE state=? AND input_path=? ORDER BY id ASC",
            (JobState.QUEUED.value, PENDING_INPUT),
        ).fetchall()
        return [_row_to_job(r) for r in rows]

    def requeue_in_progress(self) -> int:
        """Reset rebuildable in-progress jobs to QUEUED so a restart resumes them. Returns count.

        Rebuildable = {SYNTHESIZING, UPLOADING} plus any legacy 'packaging' rows from a pre-002 DB.
        The per-copy delivery flags are deliberately NOT reset, so a requeued UPLOADING job re-sends
        only the copy that had not yet gone out (exactly-once on resume; research D8).
        """
        values = [s.value for s in _REBUILDABLE] + list(_LEGACY_REBUILDABLE)
        placeholders = ",".join("?" for _ in values)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE jobs SET state=?, updated_at=? WHERE state IN ({placeholders})",
                (JobState.QUEUED.value, _now(), *values),
            )
            return cur.rowcount

    def prune_terminal_jobs(self, *, keep: int) -> int:
        """Delete all but the `keep` most-recent terminal (cleaned/failed) rows. Returns #deleted.

        Bounds datastore growth while retaining recent observability; active jobs are never pruned
        (research D10). `keep` is clamped to >= 0.
        """
        keep = max(0, keep)
        term_values = [s.value for s in _TERMINAL]
        placeholders = ",".join("?" for _ in term_values)
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM jobs WHERE state IN ({placeholders}) AND id NOT IN "
                f"(SELECT id FROM jobs WHERE state IN ({placeholders}) ORDER BY id DESC LIMIT ?)",
                (*term_values, *term_values, keep),
            )
            return cur.rowcount
