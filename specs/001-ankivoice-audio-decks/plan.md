# Implementation Plan: AnkiVoice — Audio-Enhanced Anki Decks

**Branch**: `001-ankivoice-audio-decks` | **Date**: 2026-06-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-ankivoice-audio-decks/spec.md`

## Summary

AnkiVoice is a chat bot that converts a tab-separated Anki text export into an importable `.apkg`
where each card auto-plays clear, native-accent English audio of its answer sentence on reveal (with a
replay button), while preserving the user's original text exactly. Speech is generated locally and
offline with **Kokoro-82M on CPU** (default voice `af_heart`, 24 kHz), encoded to **MP3 via an ffmpeg
subprocess**, and packaged with **genanki** (answer-side `[sound:]` tag for Anki's native auto-play +
replay). Requests are serialized through a **durable SQLite job queue** with **exactly one synthesis
worker**; delivery (archive copy first, then user) runs as a separate step that overlaps the next
synthesis. Every job's files are cleaned up after delivery on both success and failure, scoped strictly
to that job's working directory. Built test-first with a fast, fully-offline default suite (fake
Kokoro + fake Telegram) and one self-skipping live end-to-end test. Delivered via
**python-telegram-bot 22.8** long-polling. All identifiers/versions are pinned in
[research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.12 (`requires-python = ">=3.12,<3.13"`), managed with `uv`.

**Primary Dependencies** (pinned in research.md): kokoro 0.9.4 (+ misaki 0.9.4, torch 2.12.0 CPU),
genanki 0.13.1, python-telegram-bot[ext] 22.8, soundfile 0.14.0, numpy 2.4.6. System: ffmpeg
(libmp3lame) + espeak-ng. Dev: pytest 9.1.0, pytest-asyncio 1.4.0, pytest-mock 3.15.1. Config:
python-dotenv (load `.env`; env is still authoritative).

**Storage**: One SQLite database = the durable job store (the only datastore). On-disk per-job working
directories under `WORK_DIR` for transient inputs/audio/outputs.

**Testing**: pytest. Default suite is fast + fully offline — Kokoro is faked (a `FakeSynthesizer`
returning deterministic samples) and Telegram is faked (a `FakeSender` / no network). One
`@pytest.mark.live`, self-skipping test exercises real Kokoro synthesis + a real `.apkg` import, kept
out of the default run (deselected by default via pytest config).

**Target Platform**: Linux server — a single shared CPU core, ~4 GB RAM, ~40 GB disk ($6/mo VPS).
Dev/CI on macOS works identically (verified).

**Project Type**: Single project — a long-polling chat-bot service (no web/mobile frontends).

**Performance Goals**: Predictable degradation over peak throughput (P1). Exactly one synthesis at a
time; queue acknowledgement to the user within ~5 s (SC-005); the service must never OOM or fill disk
under bursty/sequential load (SC-007). Synthesis throughput is whatever one CPU core yields; it is
bounded, not maximized.

**Constraints**: 1 shared core (`torch.set_num_threads(1)`), ~4 GB RAM (model loaded once, streamed
per-sentence audio, per-job dedupe cache), ~40 GB disk (flat — every job dir removed after delivery,
scoped). Offline synthesis (no per-request cloud cost; user text never leaves the server except to the
user + operator archive). Secrets/config via environment only.

**Scale/Scope**: Small public bot. Per-job cap `MAX_CARDS` (default 200) and `MAX_FILE_BYTES`
(default 2 MB) keep each job and the produced `.apkg` bounded (well under Telegram's ~20 MB download /
~50 MB upload limits). Concurrency scale = 1 synthesis; unbounded *queue* depth (durable, FCFS).

## Constitution Check

*GATE: must pass before Phase 0 research and re-checked after Phase 1 design.* All eight principles are
satisfied by design; no violations, so Complexity Tracking is empty.

| Principle | How the design satisfies it | Verdict |
|---|---|---|
| **I. Resource-Bounded** | Single worker; one synthesis at a time via `asyncio.to_thread`; `torch.set_num_threads(1)`; model loaded once; per-sentence streaming + per-job dedupe; durable queue absorbs bursts; `MAX_CARDS`/`MAX_FILE_BYTES` caps; scoped cleanup keeps disk flat. | PASS |
| **II. Agent-Native** | 14 single-responsibility modules (config, errors, models, parser, speech, audio, packaging, pipeline, store, cleanup, delivery, worker, bot, and — cycle 002 — preflight) each independently testable; small explicit interfaces (contracts/module-interfaces.md). | PASS |
| **III. Additive, Non-Breaking** | End-to-end pipeline test guards ingest→synthesize→package→deliver; every story ships with tests; new work extends modules behind their interfaces. | PASS |
| **IV. Local-First, Offline** | Kokoro on CPU, offline after warm-up (`HF_HUB_OFFLINE=1`); no cloud TTS; user text only ever sent to the requesting user + operator archive. | PASS |
| **V. Always Clean Up, Scoped** | `cleanup.remove_job_dir` asserts the target is inside `WORK_DIR` (resolved, no symlink escape) and removes only the job dir; runs on success and failure; never deletes outside scope. | PASS |
| **VI. Durable, Resumable, Fair** | SQLite job store persists state; startup requeues non-terminal in-progress jobs; FCFS by id; at most one active job per user enforced at enqueue. | PASS |
| **VII. Test-First (NON-NEGOTIABLE)** | tasks.md orders every load-bearing behavior test-first (failing test → minimal code → refactor); fast offline default suite + one self-skipping live test. | PASS |
| **VIII. Config/Secrets via Env** | `config.load_config` reads all `ANKIVOICE_*` keys from the environment; no hard-coded secrets; `.env.example` shipped. | PASS |

**Post-Phase-1 re-check**: design artifacts (data-model, contracts, this plan) introduce no new
datastore, no extra concurrency, no out-of-scope features → still PASS. Complexity Tracking: none.

## Project Structure

### Documentation (this feature)

```text
specs/001-ankivoice-audio-decks/
├── plan.md              # This file
├── research.md          # Phase 0 — pinned versions/APIs (verified)
├── data-model.md        # Phase 1 — Job state machine + entities
├── quickstart.md        # Phase 1 — run + validate guide
├── contracts/
│   ├── module-interfaces.md   # internal agent-native module interfaces
│   └── bot-interface.md       # user-facing chat contract
├── checklists/
│   └── requirements.md        # spec quality checklist (16/16)
└── tasks.md             # Phase 2 — /speckit-tasks output (test-first)
```

### Source Code (repository root)

```text
src/ankivoice/
├── __init__.py
├── __main__.py        # entrypoint: load config → init store (resume) → build synth → run bot
├── config.py          # P8: load all ANKIVOICE_* from env (+ optional .env)
├── errors.py          # ValidationError(code, user_message) — friendly, actionable
├── models.py          # Card, ParsedDeck, Job, JobState
├── parser.py          # deck parse/validate + clean_for_speech (load-bearing)
├── speech.py          # Synthesizer Protocol + KokoroSynthesizer (load-bearing)
├── audio.py           # encode_mp3() via ffmpeg subprocess (pure)
├── packaging.py       # build_apkg() with genanki, answer-side [sound:] (load-bearing)
├── pipeline.py        # build_package(): synchronous parse→synth→encode→package core (load-bearing)
├── store.py           # JobStore: SQLite durable queue + state machine (load-bearing)
├── cleanup.py         # remove_job_dir(): scoped deletion guarantee (load-bearing)
├── delivery.py        # deliver(): archive→user→clean; Sender Protocol (load-bearing)
├── worker.py          # Worker: single FCFS synthesis loop + delivery overlap (load-bearing)
├── bot.py             # PTB Application, handlers, TelegramSender, worker wiring
└── preflight.py       # cycle 002: fail-fast startup guard (espeak-ng + ffmpeg + voice/model offline)

scripts/
└── warmup.py          # one-time online download of weights + voices + spaCy model

tests/
├── conftest.py        # fixtures: tmp work dir, FakeSynthesizer, FakeSender, sample deck
├── fixtures/
│   └── sample_deck.txt        # tab-separated export (with #headers, entities, quotes, empty Back)
├── unit/
│   ├── test_config.py
│   ├── test_parser.py
│   ├── test_speech_wrapper.py   # fake model boundary + dedupe/caching
│   ├── test_audio.py
│   ├── test_packaging.py        # builds .apkg, unzips, asserts afmt [sound:] + media
│   ├── test_store.py            # queue/state/one-active-per-user/FCFS/queue-position
│   ├── test_cleanup.py          # scope assertion + success/failure cleanup
│   ├── test_worker.py           # one-at-a-time, resume, failure path
│   └── test_delivery.py         # archive-before-user, retain-on-failure, clean-after-both
├── integration/
│   ├── test_store_resume.py     # restart requeues in-progress jobs
│   ├── test_bot_handlers.py     # size reject, one-active decline, enqueue+position (faked PTB)
│   └── test_pipeline_e2e.py     # sample deck in → importable .apkg with playable audio out (fake synth)
└── live/
    └── test_live_kokoro_apkg.py # @pytest.mark.live, self-skipping: real Kokoro + real .apkg import

.env.example          # every ANKIVOICE_* key documented (no secrets)
README.md             # quickstart, env vars, run, tests, manual test plan
pyproject.toml        # deps + pytest config (markers, default deselect of 'live')
```

**Structure Decision**: Single project, flat `src/ankivoice/` package of small single-responsibility
modules (Constitution P2). Tests mirror the modules (unit), the cross-module flows (integration), and
the real engines (live, opt-in). This matches the brief's prescribed repo shape exactly.

## Architecture (how the pieces interact)

**Ingest** (`bot.py`): a document message → reject if `file_size > MAX_FILE_BYTES` (TOO_LARGE); else if
`store.has_active_job(user)` decline (FR-020); else download into a new `WORK_DIR/job_<id>/`, enqueue,
reply queue position.

**Process** (`worker.py`, one coroutine): `store.claim_next()` (FCFS) → run
`pipeline.build_package(...)` **in a thread** (`asyncio.to_thread`, one job at a time). `pipeline`
runs `parser.parse_deck` → for each unique `spoken` (sha256 dedupe) `speech.synthesize` +
`audio.encode_mp3` → `packaging.build_apkg`. On `ValidationError`/failure: mark `failed`, message the
user, scoped-clean. Then dispatch delivery as a separate task and loop.

**Deliver** (`delivery.py`, separate task → overlaps next synthesis, FR-019): `send_document` to the
archive chat first, then to the user, then the "ready" message; only after **both** uploads succeed,
mark `delivered` → `cleanup.remove_job_dir` → `cleaned`. On upload failure: retain for resume (FR-026).

**Persist/Resume** (`store.py`): all state in SQLite (WAL, single connection on the event-loop thread).
Startup calls `requeue_in_progress()` so a restart resumes pending work (FR-021).

**Clean** (`cleanup.py`): the single chokepoint for deletion; asserts scope before `rmtree` (P5).

## Phase 0 — Outline & Research

Complete. See [research.md](./research.md): Kokoro (CPU, offline, `af_heart`, 24 kHz), MP3 (ffmpeg +
libmp3lame via stdin), genanki (answer-side `[sound:]` auto-play + replay, deterministic ids),
python-telegram-bot 22.8 (long-polling, `post_init` worker, `to_thread` for CPU work), concurrency
model, restart-resume tradeoff, and resolved config defaults. All verified against installed packages.

## Phase 1 — Design & Contracts

Complete: [data-model.md](./data-model.md) (Job entity, JobState machine, invariants, queue position),
[contracts/module-interfaces.md](./contracts/module-interfaces.md) (the module interfaces),
[contracts/bot-interface.md](./contracts/bot-interface.md) (user-facing chat contract),
[quickstart.md](./quickstart.md) (run + validation). Agent context (`CLAUDE.md`) updated to point at
this plan.

## Complexity Tracking

No constitution violations — no entries. The design is the simplest that satisfies all principles
(single worker, single datastore, no speculative abstractions; Constitution simplicity rule).
