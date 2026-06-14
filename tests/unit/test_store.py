"""T020 — durable SQLite job store + state machine (load-bearing). FR-017,018,020."""

from ankivoice.models import JobState
from ankivoice.store import JobStore


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


def test_set_state_and_list_active(tmp_path):
    s = _store(tmp_path)
    a = s.enqueue(user_id=1, chat_id=1, input_path="/a", original_filename=None)
    s.set_state(a.id, JobState.PACKAGING)
    assert s.get(a.id).state == JobState.PACKAGING
    assert [j.id for j in s.list_active()] == [a.id]
    s.set_state(a.id, JobState.CLEANED)
    assert s.list_active() == []
