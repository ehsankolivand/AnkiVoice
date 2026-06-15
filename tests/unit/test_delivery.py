"""T031 — delivery + cleanup orchestration (load-bearing). FR-022,023,026,027,029."""

import pytest

from ankivoice.delivery import deliver
from ankivoice.models import JobState
from ankivoice.store import JobStore


def _setup(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    store = JobStore(tmp_path / "jobs.sqlite")
    job = store.enqueue(user_id=7, chat_id=700, input_path="/x", original_filename="vocab.txt")
    job_dir = work / f"job_{job.id}"
    job_dir.mkdir()
    apkg = job_dir / "vocab.apkg"
    apkg.write_bytes(b"PK\x03\x04 fake apkg bytes")
    return work, store, job, apkg


async def test_archive_before_user_then_ready_then_clean(tmp_path, fake_sender):
    work, store, job, apkg = _setup(tmp_path)
    await deliver(job, apkg, sender=fake_sender, store=store, archive_chat_id=999, work_root=work)

    docs = fake_sender.documents
    assert docs[0][1] == 999  # archive FIRST (FR-022)
    assert docs[1][1] == 700  # user SECOND
    assert any(e[0] == "message" and e[1] == 700 and "ready" in e[2].lower() for e in fake_sender.events)
    # cleaned only after BOTH uploads (FR-023)
    assert store.get(job.id).state == JobState.CLEANED
    assert not (work / f"job_{job.id}").exists()  # disk returns to baseline (SC-006)


async def test_only_archive_and_user_receive_content(tmp_path, fake_sender):
    # privacy boundary: nothing leaves the server except to the user + operator archive (FR-029)
    work, store, job, apkg = _setup(tmp_path)
    await deliver(job, apkg, sender=fake_sender, store=store, archive_chat_id=999, work_root=work)
    assert {d[1] for d in fake_sender.documents} == {999, 700}


async def test_archive_failure_retains_package_for_resume(tmp_path, make_sender):
    work, store, job, apkg = _setup(tmp_path)
    sender = make_sender(fail_on_chat=999)  # archive upload fails
    with pytest.raises(RuntimeError):
        await deliver(job, apkg, sender=sender, store=store, archive_chat_id=999, work_root=work)
    assert store.get(job.id).state != JobState.CLEANED  # NOT cleaned (FR-026)
    assert (work / f"job_{job.id}").exists() and apkg.exists()  # retained for resume


async def test_user_failure_after_archive_retains_and_flags_archive_sent(tmp_path, make_sender):
    work, store, job, apkg = _setup(tmp_path)
    sender = make_sender(fail_on_chat=700)  # user upload fails (after archive succeeded)
    with pytest.raises(RuntimeError):
        await deliver(job, apkg, sender=sender, store=store, archive_chat_id=999, work_root=work)
    assert any(d[1] == 999 for d in sender.documents)  # archive got it
    assert store.get(job.id).state != JobState.CLEANED
    assert (work / f"job_{job.id}").exists()  # retained
    # cycle 002: the archive copy is recorded so a retry/resume won't re-send it (exactly-once)
    assert store.get(job.id).archive_sent is True
    assert store.get(job.id).user_sent is False


# --- cycle 002: exactly-once / idempotent re-delivery (audit D1, IR-014) ---

async def test_redelivery_after_archive_sent_only_sends_user(tmp_path, fake_sender):
    work, store, job, apkg = _setup(tmp_path)
    store.set_delivery_flag(job.id, archive=True)  # archive already went out before a crash
    await deliver(job, apkg, sender=fake_sender, store=store, archive_chat_id=999, work_root=work)
    # ONLY the user copy is sent on the resumed delivery — the archive is NOT re-sent
    assert {d[1] for d in fake_sender.documents} == {700}
    assert store.get(job.id).state == JobState.CLEANED
    assert not (work / f"job_{job.id}").exists()


async def test_redelivery_when_both_sent_sends_nothing_and_cleans(tmp_path, fake_sender):
    work, store, job, apkg = _setup(tmp_path)
    store.set_delivery_flag(job.id, archive=True)
    store.set_delivery_flag(job.id, user=True)  # both already delivered before a crash
    await deliver(job, apkg, sender=fake_sender, store=store, archive_chat_id=999, work_root=work)
    assert fake_sender.documents == []  # nothing re-sent to anyone
    assert store.get(job.id).state == JobState.CLEANED
    assert not (work / f"job_{job.id}").exists()
