"""Entrypoint: ``python -m ankivoice``.

Loads config from the environment, opens the durable store, builds the (lazy) Kokoro synthesizer and
the long-polling Telegram application, and runs it. Resuming interrupted work happens in the worker's
startup (``Worker.resume``). ``run_polling`` is blocking and owns the event loop (research.md).
"""

from __future__ import annotations

import logging

from .bot import build_application
from .config import load_config
from .speech import KokoroSynthesizer
from .store import JobStore


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()
    store = JobStore(config.db_path)
    synthesizer = KokoroSynthesizer(
        voice=config.default_voice, lang_code=config.lang_code, model_dir=config.model_dir
    )
    app = build_application(config, store, synthesizer)
    logging.getLogger("ankivoice").info("Starting AnkiVoice (long-polling)…")
    app.run_polling()


if __name__ == "__main__":
    main()
