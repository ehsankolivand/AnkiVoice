"""T024 — single synthesis worker (load-bearing). FR-017,019,028; SC-004,007.

Worker is driven with FakeSynthesizer + FakeSender + a real tmp store. CPU work is faked, so these
run fast and offline.
"""

import asyncio
import dataclasses
import threading

import numpy as np
import pytest

from ankivoice.config import Config
from ankivoice.models import JobState
from ankivoice.store import PENDING_INPUT, JobStore
from ankivoice.worker import Worker
from tests.conftest import FakeSender, FakeSynthesizer


def make_config(tmp_path, work):
    return Config(
        bot_token="t",
        archive_chat_id=999,
        default_voice="af_heart",
        lang_code="a",
        max_cards=200,
        max_file_bytes=2_000_000,
        work_dir=work,
        db_path=tmp_path / "jobs.sqlite",
        model_dir=None,
        sample_rate=24000,
        mp3_quality="4",
    )


def enqueue_job(store, work, *, user_id, chat_id, content: bytes, filename="vocab.txt"):
    job = store.enqueue(user_id=user_id, chat_id=chat_id, input_path="pending", original_filename=filename)
    job_dir = work / f"job_{job.id}"
    job_dir.mkdir(parents=True)
    inp = job_dir / "input.txt"
    inp.write_bytes(content)
    store.set_input_path(job.id, str(inp))
    return store.get(job.id)


async def _drain_deliveries(worker):
    if worker._delivery_tasks:
        await asyncio.gather(*list(worker._delivery_tasks), return_exceptions=True)


async def test_happy_path_synthesizes_packages_delivers_cleans(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    store = JobStore(tmp_path / "jobs.sqlite")
    cfg = make_config(tmp_path, work)
    sender = FakeSender()
    worker = Worker(store=store, synthesizer=FakeSynthesizer(), sender=sender, config=cfg)

    job = enqueue_job(store, work, user_id=1, chat_id=100, content=b"q\tHello there.\n")
    claimed = store.claim_next()
    assert claimed.id == job.id
    await worker._process(claimed)
    await _drain_deliveries(worker)

    assert store.get(job.id).state == JobState.CLEANED
    assert {d[1] for d in sender.documents} == {999, 100}  # archive + user
    assert not (work / f"job_{job.id}").exists()  # cleaned (SC-006)


async def test_processing_failure_marks_failed_notifies_and_cleans(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    store = JobStore(tmp_path / "jobs.sqlite")
    cfg = make_config(tmp_path, work)
    sender = FakeSender()
    worker = Worker(store=store, synthesizer=FakeSynthesizer(), sender=sender, config=cfg)

    # zero usable cards -> ValidationError(EMPTY)
    job = enqueue_job(store, work, user_id=2, chat_id=200, content=b"a\t\nb\t\n")
    claimed = store.claim_next()
    await worker._process(claimed)
    await _drain_deliveries(worker)

    got = store.get(job.id)
    assert got.state == JobState.FAILED
    assert got.error_reason == "EMPTY"
    assert any(e[0] == "message" and e[1] == 200 for e in sender.events)  # user told why
    assert not (work / f"job_{job.id}").exists()  # no residual files (FR-024)
    assert sender.documents == []  # nothing delivered


async def test_resume_abandons_interrupted_uploads(tmp_path):
    # Regression (self-review HIGH): an upload interrupted before its input was saved must be cleaned
    # and failed on restart so the user is unblocked.
    work = tmp_path / "work"
    work.mkdir()
    store = JobStore(tmp_path / "jobs.sqlite")
    cfg = make_config(tmp_path, work)
    job = store.enqueue(user_id=1, chat_id=1, input_path=PENDING_INPUT, original_filename=None)
    job_dir = work / f"job_{job.id}"
    job_dir.mkdir()
    (job_dir / "partial").write_bytes(b"x")
    worker = Worker(store=store, synthesizer=FakeSynthesizer(), sender=FakeSender(), config=cfg)

    await worker.resume()

    assert store.get(job.id).state == JobState.FAILED
    assert not job_dir.exists()
    assert store.has_active_job(1) is False  # user unblocked


async def test_resume_cleans_delivered_but_uncleaned(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    store = JobStore(tmp_path / "jobs.sqlite")
    cfg = make_config(tmp_path, work)
    job = store.enqueue(user_id=1, chat_id=1, input_path="/x", original_filename=None)
    store.set_state(job.id, JobState.DELIVERED)
    job_dir = work / f"job_{job.id}"
    job_dir.mkdir()
    (job_dir / "deck.apkg").write_bytes(b"x")
    worker = Worker(store=store, synthesizer=FakeSynthesizer(), sender=FakeSender(), config=cfg)

    await worker.resume()

    assert store.get(job.id).state == JobState.CLEANED  # cleaned, NOT re-delivered
    assert not job_dir.exists()


async def test_resume_prunes_terminal_jobs_to_bound(tmp_path):
    # cycle 002 (audit D4/IR-013): resume() bounds the job table via prune_terminal_jobs(config.job_history).
    work = tmp_path / "work"
    work.mkdir()
    store = JobStore(tmp_path / "jobs.sqlite")
    cfg = dataclasses.replace(make_config(tmp_path, work), job_history=2)
    for _ in range(5):
        j = store.enqueue(user_id=7, chat_id=1, input_path="/p", original_filename=None)
        store.set_state(j.id, JobState.CLEANED)
    worker = Worker(store=store, synthesizer=FakeSynthesizer(), sender=FakeSender(), config=cfg)

    await worker.resume()

    remaining = store._conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
    assert remaining == 2  # bounded to job_history


async def test_one_synthesis_at_a_time_and_fcfs_under_burst(tmp_path):
    # SC-007 (burst) + SC-004/FR-017 (one-at-a-time, FCFS): enqueue several, run the loop, assert
    # synthesis never overlaps and runs in arrival order, and the work dir returns to baseline.
    work = tmp_path / "work"
    work.mkdir()
    store = JobStore(tmp_path / "jobs.sqlite")
    cfg = make_config(tmp_path, work)
    sender = FakeSender()

    concurrency = {"now": 0, "max": 0}
    order: list[str] = []
    lock = threading.Lock()

    class TrackingSynth(FakeSynthesizer):
        def synthesize(self, spoken_text):
            with lock:
                concurrency["now"] += 1
                concurrency["max"] = max(concurrency["max"], concurrency["now"])
                order.append(spoken_text)
            try:
                return super().synthesize(spoken_text)
            finally:
                with lock:
                    concurrency["now"] -= 1

    worker = Worker(store=store, synthesizer=TrackingSynth(), sender=sender, config=cfg)

    jobs = [
        enqueue_job(store, work, user_id=i, chat_id=1000 + i, content=f"q\tSentence number {i}.\n".encode())
        for i in range(5)
    ]

    stop = asyncio.Event()
    run_task = asyncio.create_task(worker.run(stop))
    # wait until all jobs reach a terminal state
    for _ in range(200):
        if all(store.get(j.id).state in (JobState.CLEANED, JobState.FAILED) for j in jobs):
            break
        await asyncio.sleep(0.02)
    stop.set()
    await run_task

    assert all(store.get(j.id).state == JobState.CLEANED for j in jobs)
    assert concurrency["max"] == 1  # never two syntheses at once (FR-017)
    assert order == [f"Sentence number {i}." for i in range(5)]  # FCFS
    assert list(work.glob("job_*")) == []  # disk back to baseline (SC-006, SC-007)


async def test_delivery_overlaps_next_synthesis(tmp_path):
    # FR-019: while job A is being delivered (gated), the worker can synthesize job B.
    work = tmp_path / "work"
    work.mkdir()
    store = JobStore(tmp_path / "jobs.sqlite")
    cfg = make_config(tmp_path, work)

    gate = asyncio.Event()

    class GatedSender(FakeSender):
        async def send_document(self, chat_id, path, *, filename, caption=None):
            if chat_id == 999:  # block the FIRST archive upload until released
                await gate.wait()
            await super().send_document(chat_id, path, filename=filename, caption=caption)

    synth = FakeSynthesizer()
    worker = Worker(store=store, synthesizer=synth, sender=GatedSender(), config=cfg)

    a = enqueue_job(store, work, user_id=1, chat_id=100, content=b"q\tAlpha sentence.\n")
    b = enqueue_job(store, work, user_id=2, chat_id=200, content=b"q\tBeta sentence.\n")

    stop = asyncio.Event()
    run_task = asyncio.create_task(worker.run(stop))

    # Wait until B has been synthesized (its text appears) while A's delivery is still gated.
    for _ in range(200):
        if "Beta sentence." in synth.calls:
            break
        await asyncio.sleep(0.02)

    assert "Beta sentence." in synth.calls  # B synthesized while A delivery pending (overlap)
    assert store.get(a.id).state in (JobState.UPLOADING, JobState.DELIVERED)  # A not yet cleaned

    gate.set()  # release A's delivery
    for _ in range(200):
        if all(store.get(j.id).state == JobState.CLEANED for j in (a, b)):
            break
        await asyncio.sleep(0.02)
    stop.set()
    await run_task

    assert store.get(a.id).state == JobState.CLEANED
    assert store.get(b.id).state == JobState.CLEANED
