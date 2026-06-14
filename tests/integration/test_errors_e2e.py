"""T034 — friendly, actionable errors end-to-end through handler + worker. FR-004..007, SC-009."""

import asyncio
from pathlib import Path

from ankivoice.bot import on_document
from ankivoice.config import Config
from ankivoice.store import JobStore
from ankivoice.worker import Worker
from tests.conftest import FakeSender, FakeSynthesizer


# --- minimal PTB fakes ---

class _FakeFile:
    def __init__(self, content):
        self.content = content

    async def download_to_drive(self, custom_path):
        Path(custom_path).write_bytes(self.content)
        return Path(custom_path)


class _FakeDoc:
    def __init__(self, content, file_name="deck.txt", file_size=None):
        self.content = content
        self.file_name = file_name
        self.file_size = len(content) if file_size is None else file_size

    async def get_file(self):
        return _FakeFile(self.content)


class _FakeMsg:
    def __init__(self, doc):
        self.document = doc
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class _Id:
    def __init__(self, id):
        self.id = id


class _FakeUpdate:
    def __init__(self, doc, user_id=1, chat_id=100):
        self.message = _FakeMsg(doc)
        self.effective_user = _Id(user_id)
        self.effective_chat = _Id(chat_id)


class _FakeContext:
    def __init__(self, store, config):
        self.bot_data = {"store": store, "config": config}


def _config(tmp_path, work, *, max_cards=200, max_file_bytes=2_000_000):
    return Config(
        bot_token="t", archive_chat_id=999, default_voice="af_heart", lang_code="a",
        max_cards=max_cards, max_file_bytes=max_file_bytes, work_dir=work,
        db_path=tmp_path / "jobs.sqlite", model_dir=None, sample_rate=24000, mp3_quality="4",
    )


async def _submit_and_process(content, *, store, config, sender):
    """Run the upload handler, then the worker on any resulting job. Returns (update, sender)."""
    upd = _FakeUpdate(_FakeDoc(content))
    await on_document(upd, _FakeContext(store, config))
    worker = Worker(store=store, synthesizer=FakeSynthesizer(), sender=sender, config=config)
    job = store.claim_next()
    if job is not None:
        await worker._process(job)
        if worker._delivery_tasks:
            await asyncio.gather(*list(worker._delivery_tasks), return_exceptions=True)
    return upd


def _all_messages(update, sender):
    return [r.lower() for r in update.message.replies] + [
        e[2].lower() for e in sender.events if e[0] == "message"
    ]


async def test_wrong_format_friendly_message_and_no_residue(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    store = JobStore(tmp_path / "jobs.sqlite")
    sender = FakeSender()
    upd = await _submit_and_process(b"just one column\nanother line\n", store=store, config=_config(tmp_path, work), sender=sender)
    msgs = _all_messages(upd, sender)
    assert any("tab" in m or "format" in m for m in msgs)
    assert store.list_active() == []  # service healthy, no stuck job
    assert list(work.glob("job_*")) == []  # no residual files (SC-009)
    assert sender.documents == []  # nothing delivered


async def test_empty_deck_friendly_message(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    store = JobStore(tmp_path / "jobs.sqlite")
    sender = FakeSender()
    upd = await _submit_and_process(b"a\t\nb\t\n", store=store, config=_config(tmp_path, work), sender=sender)
    msgs = _all_messages(upd, sender)
    assert any("usable" in m or "empty" in m or "answer" in m for m in msgs)
    assert list(work.glob("job_*")) == []


async def test_too_many_cards_friendly_message(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    store = JobStore(tmp_path / "jobs.sqlite")
    sender = FakeSender()
    cfg = _config(tmp_path, work, max_cards=2)
    upd = await _submit_and_process(b"f1\tb1\nf2\tb2\nf3\tb3\n", store=store, config=cfg, sender=sender)
    msgs = _all_messages(upd, sender)
    assert any("too many" in m or "limit" in m for m in msgs)
    assert list(work.glob("job_*")) == []


async def test_too_large_rejected_at_handler(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    store = JobStore(tmp_path / "jobs.sqlite")
    sender = FakeSender()
    cfg = _config(tmp_path, work, max_file_bytes=10)
    upd = await _submit_and_process(b"x" * 100, store=store, config=cfg, sender=sender)
    assert any("too large" in r.lower() for r in upd.message.replies)
    assert store.list_active() == []
    assert list(work.glob("job_*")) == []  # never created
