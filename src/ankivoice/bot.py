"""Telegram layer (long-polling): handlers + TelegramSender + worker wiring (research.md Decision 4).

Handlers are module functions reading ``context.bot_data`` (so they are unit-testable with faked
Update/Context). The single synthesis worker is started as a long-lived task in ``post_init`` (the app
is running by then) and stopped in ``post_shutdown``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .cleanup import remove_job_dir
from .config import Config
from .models import JobState
from .store import PENDING_INPUT, JobStore
from .worker import Worker

logger = logging.getLogger("ankivoice.bot")

HELP_TEXT = (
    "👋 I turn a text Anki deck into an audio deck with native English speech.\n\n"
    "Send me a tab-separated Anki export (Front⇥Back, where Back is the full answer sentence). "
    "I'll reply with an .apkg — when you reveal each answer the audio plays automatically, with a "
    "replay button.\n\n"
    "I work on one deck at a time; you'll get your place in line when you send a file."
)


def _mb(num_bytes: int) -> str:
    return f"{num_bytes / 1_000_000:.1f}"


async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT)


async def on_document(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store: JobStore = context.bot_data["store"]
    config: Config = context.bot_data["config"]
    doc = update.message.document
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # 1) too large — reject before downloading (Bot API cap; FR-006)
    if doc.file_size is not None and doc.file_size > config.max_file_bytes:
        await update.message.reply_text(
            f"That file is too large ({_mb(doc.file_size)} MB); the limit is "
            f"{_mb(config.max_file_bytes)} MB. Please send a smaller export."
        )
        return

    # 2) accept iff the user has no active job — ATOMIC check-and-reserve in one transaction (FR-020).
    #    The slot is reserved (input_path PENDING so the worker can't claim it mid-download) BEFORE the
    #    download, so a refusal never leaves an orphaned file and two near-simultaneous uploads from the
    #    same user can't both create an active job (cycle 002, audit D2).
    job = store.enqueue_if_no_active(
        user_id=user_id, chat_id=chat_id, input_path=PENDING_INPUT, original_filename=doc.file_name
    )
    if job is None:
        await update.message.reply_text(
            "You already have a deck being processed — I work on one at a time. "
            "Please wait for it to finish before sending another."
        )
        return

    # 3) save the upload inside the job dir, then mark it claimable and reply the queue position (FR-018).
    job_dir = Path(config.work_dir) / f"job_{job.id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    input_path = job_dir / "input.txt"
    try:
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(str(input_path))
    except Exception:
        logger.exception("failed to download upload for job %s", job.id)
        store.set_state(job.id, JobState.FAILED, error_reason="download_failed")
        try:  # scoped cleanup so the failed download leaves no residual files (FR-024)
            remove_job_dir(job_dir, work_root=config.work_dir)
        except Exception:
            logger.exception("failed to clean job dir for job %s", job.id)
        await update.message.reply_text(
            "Sorry, I couldn't download that file. Please try sending it again."
        )
        return
    store.set_input_path(job.id, str(input_path))
    position = store.queue_position(job.id)
    await update.message.reply_text(
        f"Got it! Your deck is #{position} in line. I'll send it back when it's ready."
    )


class TelegramSender:
    """Implements ``delivery.Sender`` using the PTB bot."""

    def __init__(self, bot) -> None:
        self._bot = bot

    async def send_document(self, chat_id: int, path, *, filename: str, caption: str | None = None) -> None:
        await self._bot.send_document(
            chat_id=chat_id,
            document=Path(path),
            filename=filename,
            caption=caption,
            read_timeout=120,
            write_timeout=120,
        )

    async def send_message(self, chat_id: int, text: str) -> None:
        await self._bot.send_message(chat_id=chat_id, text=text)


async def _post_init(app: Application) -> None:
    config: Config = app.bot_data["config"]
    store: JobStore = app.bot_data["store"]
    synthesizer = app.bot_data["synthesizer"]
    sender = TelegramSender(app.bot)
    stop = asyncio.Event()
    worker = Worker(store=store, synthesizer=synthesizer, sender=sender, config=config)
    app.bot_data["_stop"] = stop
    # app is running here, so the task is tracked and awaited on shutdown (research.md gotcha)
    app.bot_data["_worker_task"] = app.create_task(worker.run(stop), name="ankivoice-worker")
    logger.info("AnkiVoice worker started")


async def _post_shutdown(app: Application) -> None:
    stop: asyncio.Event | None = app.bot_data.get("_stop")
    task = app.bot_data.get("_worker_task")
    if stop is not None:
        stop.set()
    if task is not None:
        try:
            await task
        except asyncio.CancelledError:
            pass


def build_application(config: Config, store: JobStore, synthesizer) -> Application:
    """Build the long-polling Application (no network performed by build())."""
    app = (
        ApplicationBuilder()
        .token(config.bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.bot_data["store"] = store
    app.bot_data["config"] = config
    app.bot_data["synthesizer"] = synthesizer
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(MessageHandler(filters.Document.ALL & ~filters.COMMAND, on_document))
    return app
