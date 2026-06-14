"""T026 — Telegram handlers (faked Update/Bot, no network). FR-006,018,020."""

from pathlib import Path

from ankivoice.bot import build_application, cmd_start, on_document
from ankivoice.config import Config
from ankivoice.store import JobStore


# --- minimal PTB fakes ---

class FakeFile:
    def __init__(self, content: bytes):
        self.content = content

    async def download_to_drive(self, custom_path):
        Path(custom_path).write_bytes(self.content)
        return Path(custom_path)


class FakeDoc:
    def __init__(self, content: bytes, file_name="vocab.txt", file_size=None):
        self.content = content
        self.file_name = file_name
        self.file_size = len(content) if file_size is None else file_size

    async def get_file(self):
        return FakeFile(self.content)


class FakeMessage:
    def __init__(self, doc):
        self.document = doc
        self.replies: list[str] = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class _Id:
    def __init__(self, id):
        self.id = id


class FakeUpdate:
    def __init__(self, doc, user_id, chat_id):
        self.message = FakeMessage(doc)
        self.effective_user = _Id(user_id)
        self.effective_chat = _Id(chat_id)


class FakeContext:
    def __init__(self, store, config):
        self.bot_data = {"store": store, "config": config}


def make_config(tmp_path, work, max_file_bytes=2_000_000):
    return Config(
        bot_token="123456:ABC-DEF", archive_chat_id=999, default_voice="af_heart", lang_code="a",
        max_cards=200, max_file_bytes=max_file_bytes, work_dir=work,
        db_path=tmp_path / "jobs.sqlite", model_dir=None, sample_rate=24000, mp3_quality="4",
    )


async def test_accepted_upload_enqueues_saves_and_replies_position(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    store = JobStore(tmp_path / "jobs.sqlite")
    cfg = make_config(tmp_path, work)
    upd = FakeUpdate(FakeDoc(b"q\tHello there.\n", "vocab.txt"), user_id=1, chat_id=100)

    await on_document(upd, FakeContext(store, cfg))

    active = store.list_active()
    assert len(active) == 1
    job = active[0]
    assert job.original_filename == "vocab.txt"
    assert job.input_path == str(work / f"job_{job.id}" / "input.txt")
    assert Path(job.input_path).read_bytes() == b"q\tHello there.\n"
    assert any("line" in r.lower() for r in upd.message.replies)  # queue-position reply (FR-018)


async def test_rejects_too_large_without_creating_job(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    store = JobStore(tmp_path / "jobs.sqlite")
    cfg = make_config(tmp_path, work, max_file_bytes=10)
    upd = FakeUpdate(FakeDoc(b"x" * 100, file_size=100), user_id=1, chat_id=100)

    await on_document(upd, FakeContext(store, cfg))

    assert store.list_active() == []  # no job created (FR-006)
    assert any("too large" in r.lower() for r in upd.message.replies)


async def test_declines_second_active_job_for_same_user(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    store = JobStore(tmp_path / "jobs.sqlite")
    cfg = make_config(tmp_path, work)
    store.enqueue(user_id=1, chat_id=100, input_path="p", original_filename=None)  # already active
    upd = FakeUpdate(FakeDoc(b"q\tHi.\n"), user_id=1, chat_id=100)

    await on_document(upd, FakeContext(store, cfg))

    assert len(store.list_active()) == 1  # still just the one (FR-020)
    assert any("already" in r.lower() for r in upd.message.replies)


async def test_start_help_replies(tmp_path):
    upd = FakeUpdate(FakeDoc(b""), 1, 1)
    await cmd_start(upd, FakeContext(None, None))
    assert len(upd.message.replies) == 1
    assert len(upd.message.replies[0]) > 0


def test_build_application_is_offline_and_wires_handlers(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    store = JobStore(tmp_path / "jobs.sqlite")
    cfg = make_config(tmp_path, work)
    app = build_application(cfg, store, synthesizer=object())
    assert app.bot_data["store"] is store
    assert app.bot_data["config"] is cfg
    assert sum(len(hs) for hs in app.handlers.values()) >= 2  # command + document handlers
