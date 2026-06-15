# Quickstart & Validation: AnkiVoice

This guide proves the feature works end-to-end. It does **not** duplicate implementation detail — see
[contracts/module-interfaces.md](./contracts/module-interfaces.md) and [research.md](./research.md).

## Prerequisites

- Python 3.12 + [`uv`](https://docs.astral.sh/uv/).
- System package **ffmpeg** (with libmp3lame) on PATH — invoked as a subprocess to encode MP3s.
  - macOS: `brew install ffmpeg` · Debian/Ubuntu VPS: `sudo apt-get install -y ffmpeg`
  - **espeak-ng is bundled** via the `espeakng_loader` dependency (installed by `uv sync`) and loaded
    in-process — no separate install / PATH entry needed (cycle 002).
- Install deps: `uv sync`
- A Telegram bot token (from @BotFather) and an operator-owned archive chat/channel id.

## Configure (environment only — Principle VIII)

Copy `.env.example` → `.env` and fill it in (or export the variables):

```bash
ANKIVOICE_BOT_TOKEN=123456:abc...           # secret, from @BotFather
ANKIVOICE_ARCHIVE_CHAT_ID=-1001234567890    # operator-owned backup destination
ANKIVOICE_DEFAULT_VOICE=af_heart            # American-English default voice
ANKIVOICE_LANG_CODE=a
ANKIVOICE_MAX_CARDS=200
ANKIVOICE_MAX_FILE_BYTES=2000000
ANKIVOICE_WORK_DIR=./work
ANKIVOICE_DB_PATH=./data/ankivoice.db
# ANKIVOICE_MODEL_DIR=./models               # optional: pin the offline model cache (sets HF_HOME)
```

## One-time offline warm-up (Principle IV)

Downloads the Kokoro weights, the voice pack(s) you will use, and the spaCy English model so the bot
runs fully offline afterward:

```bash
uv run python scripts/warmup.py            # fetches hexgrad/Kokoro-82M + voices/af_heart.pt + en_core_web_sm
# After this, the bot can run with HF_HUB_OFFLINE=1 (no network needed for synthesis).
```

## Run the bot

```bash
uv run python -m ankivoice                 # loads config, resumes pending jobs, starts long-polling
```

No public TLS/inbound port is needed (long-polling only makes outbound HTTPS calls).

## Run the tests

```bash
uv run pytest                              # fast, fully offline default suite (fake Kokoro + fake Telegram)
uv run pytest -m live                      # opt-in: real Kokoro synthesis + real .apkg import (self-skips if unavailable)
```

The default run never touches the network or loads the real model. The `live` test self-skips unless
the model/voice are cached and ffmpeg/espeak-ng are present.

## Validation scenarios (map to the spec)

1. **US1 / SC-001..003 — core conversion**: send `tests/fixtures/sample_deck.txt` to the bot; receive a
   `.apkg`; import into Anki; reveal each answer → correct native audio auto-plays and the replay
   button works; the displayed answer text is byte-for-byte the original (entities/quotes preserved in
   display, cleaned only in audio). The automated proof is `tests/integration/test_pipeline_e2e.py`
   (fake synth) plus `tests/live/test_live_kokoro_apkg.py` (real engines).
2. **US2 / SC-004,005,010 — queue**: send two files near-simultaneously → both get a queue-position
   reply; the second is synthesized only after the first finishes; the first's delivery overlaps the
   second's synthesis. Restart the bot mid-run → the unfinished deck still completes (resume). Proven
   by `test_store.py`, `test_worker.py`, `test_store_resume.py`, `test_bot_handlers.py`.
3. **US3 / SC-006,008 — archive + cleanup**: process a deck → the package reaches the archive before
   the user; after delivery, `WORK_DIR` has no `job_*` dir and disk is back to baseline; a "ready"
   message was sent. Proven by `test_delivery.py`, `test_cleanup.py`.
4. **US4 / SC-009 — friendly errors**: send a non-tab file, an empty file, an oversized file, and a
   file over the card cap → each gets a specific friendly message; the service stays up; no residual
   files. Proven by `test_parser.py`, `test_bot_handlers.py`.

## Expected outcomes

- `uv run pytest` → all green, in seconds, offline.
- A delivered `.apkg` imports cleanly; every card auto-plays its answer audio with a working replay
  button; original text unchanged.
- After any number of deliveries, `WORK_DIR` holds no per-job files (disk stays flat).
