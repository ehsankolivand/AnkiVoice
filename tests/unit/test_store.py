"""T020 — durable SQLite job store + state machine (load-bearing). FR-017,018,020.

Cycle 002 (T005/T006): atomic one-active enqueue, per-copy delivery flags, bounded prune, PACKAGING
removed, additive schema migration.
"""

import sqlite3

from ankivoice.models import JobState
from ankivoice.store import PENDING_INPUT, JobStore


def _store(tmp_path):
    return JobStore(tmp_path / "data" / "jobs.sqlite")


def test_enqueue_persists_and_get_roundtrips(tmp_path):
    s = _store(tmp_path)
    job = s.enqueue(user_id=1, chat_id=10, input_path="/work/job_1/in.txt", original_filename="in.txt")
    assert job.id >= 1
    assert job.state == JobState.QUEUED
    got = s.get(job.id)
    assert got.user_id == 1 and got.chat_id == 10
    assert got.input_path == "/work/job_1/in.txt" and got.original_filename == "in.txt"
    assert got.created_at and got.updated_at


def test_has_active_job_true_until_terminal(tmp_path):
    s = _store(tmp_path)
    assert s.has_active_job(1) is False
    j = s.enqueue(user_id=1, chat_id=10, input_path="/p", original_filename=None)
    assert s.has_active_job(1) is True
    s.set_state(j.id, JobState.CLEANED)  # terminal success
    assert s.has_active_job(1) is False
    j2 = s.enqueue(user_id=1, chat_id=10, input_path="/p", original_filename=None)
    s.set_state(j2.id, JobState.FAILED, error_reason="bad")  # terminal failure
    assert s.has_active_job(1) is False
    assert s.get(j2.id).error_reason == "bad"


def test_claim_next_is_fcfs(tmp_path):
    s = _store(tmp_path)
    a = s.enqueue(user_id=1, chat_id=1, input_path="/a", original_filename=None)
    b = s.enqueue(user_id=2, chat_id=2, input_path="/b", original_filename=None)
    c1 = s.claim_next()
    assert c1.id == a.id and c1.state == JobState.SYNTHESIZING
    c2 = s.claim_next()
    assert c2.id == b.id
    assert s.claim_next() is None  # nothing queued


def test_queue_position(tmp_path):
    s = _store(tmp_path)
    a = s.enqueue(user_id=1, chat_id=1, input_path="/a", original_filename=None)
    b = s.enqueue(user_id=2, chat_id=2, input_path="/b", original_filename=None)
    c = s.enqueue(user_id=3, chat_id=3, input_path="/c", original_filename=None)
    assert s.queue_position(a.id) == 1
    assert s.queue_position(b.id) == 2
    assert s.queue_position(c.id) == 3
    s.claim_next()  # a -> synthesizing, still counts as "ahead"
    assert s.queue_position(b.id) == 2
    assert s.queue_position(c.id) == 3


def test_claim_next_skips_jobs_whose_input_is_not_saved_yet(tmp_path):
    # Regression (self-review HIGH): a job is not claimable until its upload is saved.
    s = _store(tmp_path)
    j = s.enqueue(user_id=1, chat_id=1, input_path=PENDING_INPUT, original_filename=None)
    assert s.claim_next() is None  # still "pending" → not claimable
    s.set_input_path(j.id, "/work/job_1/input.txt")
    claimed = s.claim_next()
    assert claimed is not None and claimed.id == j.id


def test_list_abandoned_uploads(tmp_path):
    s = _store(tmp_path)
    a = s.enqueue(user_id=1, chat_id=1, input_path=PENDING_INPUT, original_filename=None)
    s.enqueue(user_id=2, chat_id=2, input_path="/real/path", original_filename=None)
    assert [j.id for j in s.list_abandoned_uploads()] == [a.id]


def test_set_state_and_list_active(tmp_path):
    s = _store(tmp_path)
    a = s.enqueue(user_id=1, chat_id=1, input_path="/a", original_filename=None)
    s.set_state(a.id, JobState.UPLOADING)
    assert s.get(a.id).state == JobState.UPLOADING
    assert [j.id for j in s.list_active()] == [a.id]
    s.set_state(a.id, JobState.CLEANED)
    assert s.list_active() == []


# --- cycle 002: atomic one-active-per-user enqueue (research D9) ---

def test_enqueue_if_no_active_refuses_second_active_job(tmp_path):
    s = _store(tmp_path)
    first = s.enqueue_if_no_active(user_id=1, chat_id=1, input_path=PENDING_INPUT, original_filename=None)
    assert first is not None
    # same user, still active -> refused atomically
    second = s.enqueue_if_no_active(user_id=1, chat_id=1, input_path=PENDING_INPUT, original_filename=None)
    assert second is None
    assert len(s.list_active()) == 1
    # a different user is allowed
    other = s.enqueue_if_no_active(user_id=2, chat_id=2, input_path=PENDING_INPUT, original_filename=None)
    assert other is not None
    # once the first user's job is terminal, they can enqueue again
    s.set_state(first.id, JobState.CLEANED)
    again = s.enqueue_if_no_active(user_id=1, chat_id=1, input_path=PENDING_INPUT, original_filename=None)
    assert again is not None


# --- cycle 002: per-copy delivery flags (research D8) ---

def test_set_delivery_flag(tmp_path):
    s = _store(tmp_path)
    j = s.enqueue(user_id=1, chat_id=1, input_path="/p", original_filename=None)
    assert s.get(j.id).archive_sent is False and s.get(j.id).user_sent is False
    s.set_delivery_flag(j.id, archive=True)
    assert s.get(j.id).archive_sent is True and s.get(j.id).user_sent is False
    s.set_delivery_flag(j.id, user=True)
    assert s.get(j.id).user_sent is True


# --- cycle 002: bounded prune of terminal jobs (research D10) ---

def test_prune_terminal_jobs_keeps_recent_and_spares_active(tmp_path):
    s = _store(tmp_path)
    terminal_ids = []
    for _ in range(6):
        j = s.enqueue(user_id=99, chat_id=1, input_path="/p", original_filename=None)
        s.set_state(j.id, JobState.CLEANED)
        terminal_ids.append(j.id)
    active = s.enqueue(user_id=1, chat_id=1, input_path="/p", original_filename=None)  # QUEUED

    deleted = s.prune_terminal_jobs(keep=2)
    assert deleted == 4
    remaining = {row["id"] for row in s._conn.execute("SELECT id FROM jobs")}
    # the 2 most-recent terminal rows + the active one survive
    assert remaining == {terminal_ids[-1], terminal_ids[-2], active.id}


# --- cycle 002: additive schema migration on an older DB ---

def test_opens_and_migrates_a_pre_002_database(tmp_path):
    db = tmp_path / "old.sqlite"
    con = sqlite3.connect(str(db))
    con.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
            input_path TEXT NOT NULL, original_filename TEXT, state TEXT NOT NULL, error_reason TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO jobs (user_id, chat_id, input_path, original_filename, state, created_at, updated_at)
        VALUES (1, 1, '/p', NULL, 'uploading', 't', 't');
        """
    )
    con.commit()
    con.close()

    s = JobStore(db)  # must migrate without error
    j = s.get(1)
    assert j is not None and j.archive_sent is False and j.user_sent is False
    s.set_delivery_flag(1, archive=True)
    assert s.get(1).archive_sent is True
