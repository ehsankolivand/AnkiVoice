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

# Non-terminal states = an "active" job for the one-job-per-user rule (FR-020).
_ACTIVE = (
    JobState.QUEUED,
    JobState.SYNTHESIZING,
    JobState.PACKAGING,
    JobState.UPLOADING,
    JobState.DELIVERED,
)
# States whose work must be rebuilt from the input file after a restart.
_REBUILDABLE = (JobState.SYNTHESIZING, JobState.PACKAGING, JobState.UPLOADING)
# Jobs "ahead in line" when computing a queue position.
_AHEAD = (JobState.QUEUED, JobState.SYNTHESIZING, JobState.PACKAGING)

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
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id);
"""


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
        self._lock = threading.Lock()

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

    def queue_position(self, job_id: int) -> int:
        placeholders = ",".join("?" for _ in _AHEAD)
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM jobs WHERE id <= ? AND state IN ({placeholders})",
            (job_id, *[s.value for s in _AHEAD]),
        ).fetchone()
        return int(row["n"])

    def claim_next(self) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE state=? ORDER BY id ASC LIMIT 1",
                (JobState.QUEUED.value,),
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

    def requeue_in_progress(self) -> int:
        """Reset rebuildable in-progress jobs to QUEUED so a restart resumes them. Returns count."""
        placeholders = ",".join("?" for _ in _REBUILDABLE)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE jobs SET state=?, updated_at=? WHERE state IN ({placeholders})",
                (JobState.QUEUED.value, _now(), *[s.value for s in _REBUILDABLE]),
            )
            return cur.rowcount
