"""T022 — durability across restart + resume of in-progress work (FR-021, SC-010)."""

from ankivoice.models import JobState
from ankivoice.store import JobStore


def test_state_persists_across_reopen(tmp_path):
    db = tmp_path / "jobs.sqlite"
    s = JobStore(db)
    j = s.enqueue(user_id=1, chat_id=1, input_path="/a", original_filename="a.txt")
    s.set_state(j.id, JobState.UPLOADING)
    s.close()

    s2 = JobStore(db)  # simulate a restart
    got = s2.get(j.id)
    assert got is not None and got.state == JobState.UPLOADING


def test_requeue_in_progress_resets_only_rebuildable_states(tmp_path):
    s = JobStore(tmp_path / "jobs.sqlite")
    ids = {}
    for uid, st in enumerate(
        [
            JobState.QUEUED,
            JobState.SYNTHESIZING,
            JobState.UPLOADING,
            JobState.DELIVERED,
            JobState.CLEANED,
            JobState.FAILED,
        ]
    ):
        j = s.enqueue(user_id=uid, chat_id=1, input_path="/p", original_filename=None)
        s.set_state(j.id, st)
        ids[st] = j.id

    n = s.requeue_in_progress()
    # SYNTHESIZING, UPLOADING -> requeued (2). DELIVERED is NOT (would double-deliver). (002: no PACKAGING)
    assert n == 2
    assert s.get(ids[JobState.SYNTHESIZING]).state == JobState.QUEUED
    assert s.get(ids[JobState.UPLOADING]).state == JobState.QUEUED
    # untouched:
    assert s.get(ids[JobState.QUEUED]).state == JobState.QUEUED
    assert s.get(ids[JobState.DELIVERED]).state == JobState.DELIVERED
    assert s.get(ids[JobState.CLEANED]).state == JobState.CLEANED
    assert s.get(ids[JobState.FAILED]).state == JobState.FAILED


def test_requeue_maps_legacy_packaging_rows(tmp_path):
    # A pre-002 DB may hold rows with the removed 'packaging' state; resume must rebuild them.
    s = JobStore(tmp_path / "jobs.sqlite")
    j = s.enqueue(user_id=1, chat_id=1, input_path="/p", original_filename=None)
    s._conn.execute("UPDATE jobs SET state='packaging' WHERE id=?", (j.id,))  # legacy value
    n = s.requeue_in_progress()
    assert n == 1
    assert s.get(j.id).state == JobState.QUEUED


def test_requeue_preserves_delivery_flags(tmp_path):
    # A requeued UPLOADING job must remember which copies already went out (exactly-once on resume).
    s = JobStore(tmp_path / "jobs.sqlite")
    j = s.enqueue(user_id=1, chat_id=1, input_path="/p", original_filename=None)
    s.set_state(j.id, JobState.UPLOADING)
    s.set_delivery_flag(j.id, archive=True)
    s.requeue_in_progress()
    got = s.get(j.id)
    assert got.state == JobState.QUEUED and got.archive_sent is True and got.user_sent is False


def test_list_in_state(tmp_path):
    s = JobStore(tmp_path / "jobs.sqlite")
    a = s.enqueue(user_id=1, chat_id=1, input_path="/a", original_filename=None)
    b = s.enqueue(user_id=2, chat_id=2, input_path="/b", original_filename=None)
    s.set_state(a.id, JobState.DELIVERED)
    delivered = s.list_in_state(JobState.DELIVERED)
    assert [j.id for j in delivered] == [a.id]
    assert {j.id for j in s.list_in_state(JobState.QUEUED)} == {b.id}
