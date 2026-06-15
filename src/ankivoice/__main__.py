"""Entrypoint: ``python -m ankivoice``.

Loads config from the environment, opens the durable store, builds the (lazy) Kokoro synthesizer and
the long-polling Telegram application, and runs it. Resuming interrupted work happens in the worker's
startup (``Worker.resume``). ``run_polling`` is blocking and owns the event loop (research.md).
"""

from __future__ import annotations

import logging
import os

from .bot import build_application
from .config import load_config
from .preflight import PreflightError, check_runtime
from .speech import KokoroSynthesizer
from .store import JobStore


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()

    # Pin the offline model cache and default the process to OFFLINE (Constitution P4) before any
    # synthesis. Set ANKIVOICE_ALLOW_DOWNLOADS=1 (e.g. for the warm-up) to permit downloads.
    if config.model_dir is not None:
        os.environ.setdefault("HF_HOME", str(config.model_dir))
    if not os.environ.get("ANKIVOICE_ALLOW_DOWNLOADS"):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    store = JobStore(config.db_path)
    synthesizer = KokoroSynthesizer(
        voice=config.default_voice, lang_code=config.lang_code, model_dir=config.model_dir
    )

    # Fail fast (and prewarm the model) BEFORE accepting any job: a missing espeak-ng silently corrupts
    # audio, a missing ffmpeg / uncached voice fails late. Refuse to start with a specific message.
    try:
        check_runtime(config, synthesizer)
    except PreflightError as exc:
        logging.getLogger("ankivoice").error("Startup preflight failed: %s", exc)
        raise SystemExit(f"AnkiVoice cannot start: {exc}") from exc

    app = build_application(config, store, synthesizer)
    logging.getLogger("ankivoice").info("Starting AnkiVoice (long-polling)…")
    app.run_polling()


if __name__ == "__main__":
    main()
