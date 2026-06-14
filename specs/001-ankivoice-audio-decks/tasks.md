---
description: "Task list for AnkiVoice — audio-enhanced Anki decks (test-first / TDD)"
---

# Tasks: AnkiVoice — Audio-Enhanced Anki Decks

**Input**: Design documents from `specs/001-ankivoice-audio-decks/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: REQUIRED and TEST-FIRST for all load-bearing paths (Constitution Principle VII,
NON-NEGOTIABLE). Each `…(failing)` test task is written and confirmed RED before the implementation
task immediately following it. The default suite is fast + fully offline (Kokoro faked/mocked,
Telegram faked); one `@pytest.mark.live` test (deselected by default) exercises real engines.

**Organization**: by user story. **US1 (P1) is the standalone MVP** (the core conversion pipeline,
testable end-to-end with a fake synthesizer, no bot/queue needed). US2 adds the durable queue + worker
+ bot; US3 adds archive delivery + scoped cleanup; US4 adds friendly error surfacing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: may run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 / US2 / US3 / US4 (omitted in Setup / Foundational / Polish)
- Every task names an exact file path.

## Path Conventions

Single project: package `src/ankivoice/`, tests `tests/{unit,integration,live}/`, fixtures
`tests/fixtures/`, helper scripts `scripts/`. (Per plan.md Project Structure.)

> Module note: `pipeline.py` is the synchronous core orchestration (`parse → synth → encode → package`)
> introduced so US1 is independently testable and the async `worker.py` stays thin (it calls
> `pipeline.build_package` via `asyncio.to_thread`). This refines plan.md's worker "SYNTHESIZE/PACKAGE"
> step into a testable unit (Constitution P2/P7); no behavior change.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: project skeleton, test harness, fixtures.

- [x] T001 Create the package + test tree: `src/ankivoice/__init__.py`, and empty dirs
  `tests/unit/`, `tests/integration/`, `tests/live/`, `tests/fixtures/`, `scripts/` (per plan.md).
- [x] T002 Configure `pyproject.toml`: confirm runtime deps (kokoro, genanki, python-telegram-bot[ext],
  soundfile, numpy, python-dotenv) and dev deps (pytest, pytest-asyncio, pytest-mock); add
  `[tool.pytest.ini_options]` with `markers = ["live: real Kokoro+apkg, opt-in"]`,
  `addopts = "-m 'not live'"`, `asyncio_mode = "auto"`, and `testpaths = ["tests"]`.
- [x] T003 [P] Create `tests/fixtures/sample_deck.txt`: a realistic tab-separated Anki export with
  leading `#separator:tab` / `#html:true` headers, several valid Front⇥Back cards, a field with HTML
  entities (`&amp;`, `&#39;`), a CSV double-quote-wrapped field, one empty-Back row, one empty-Front
  (valid Back) row, and one line with no TAB.
- [x] T004 [P] Create `tests/conftest.py`: fixtures for a temp work dir, `FakeSynthesizer`
  (deterministic float32 samples, configurable `sample_rate=24000`, counts calls for dedupe asserts),
  `FakeSender` (records ordered `send_document`/`send_message` calls, can be set to fail a given send),
  and a `sample_deck_bytes` loader.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: shared types + config that every story imports. **⚠️ Blocks all user stories.**

- [x] T005 [P] Write FAILING `tests/unit/test_config.py`: `load_config` reads all `ANKIVOICE_*` keys;
  missing required keys raise `ConfigError` naming each; optional keys get documented defaults
  (voice `af_heart`, lang `a`, max_cards 200, max_file_bytes 2_000_000, sample_rate 24000); never
  hard-codes secrets; optionally loads `.env`.
- [x] T006 Implement `src/ankivoice/config.py` (`Config` dataclass + `load_config`) to pass T005.
- [x] T007 [P] Write FAILING `tests/unit/test_models_errors.py`: `Card`/`ParsedDeck`/`Job` dataclasses
  and `JobState` enum values/transitions exist as specified; `ValidationError` carries `code` +
  `user_message`.
- [x] T008 Implement `src/ankivoice/models.py` and `src/ankivoice/errors.py` to pass T007.

**Checkpoint**: config + shared types ready — user stories can begin.

---

## Phase 3: User Story 1 - Audio-enhanced importable package (Priority: P1) 🎯 MVP

**Goal**: a valid deck file becomes an importable `.apkg` whose every card auto-plays correct
native-accent audio of the answer on reveal (with a replay button), original text preserved.

**Independent Test**: run `tests/integration/test_pipeline_e2e.py` — `sample_deck.txt` in → importable
`.apkg` with one playable audio per usable card out (fake synth, offline). Plus the live test (T039).

### Parser (load-bearing, test-first)

- [x] T009 [P] [US1] Write FAILING `tests/unit/test_parser.py`: `clean_for_speech` decodes HTML
  entities + strips one layer of CSV quotes (un-doubles `""`); `parse_deck` skips `#` headers, splits
  on first TAB (extra columns ignored), allows empty Front, skips+counts empty-Back and no-TAB rows,
  preserves original Front/Back byte-for-byte, and raises `WRONG_FORMAT` (no TAB anywhere / undecodable
  UTF-8), `EMPTY` (zero usable), `TOO_MANY_CARDS` (> max).
- [x] T010 [US1] Implement `src/ankivoice/parser.py` to pass T009.

### Speech wrapper (load-bearing, test-first)

- [x] T011 [P] [US1] Write FAILING `tests/unit/test_speech_wrapper.py`: with `kokoro.KPipeline` MOCKED
  (no model/network), `KokoroSynthesizer` builds the pipeline ONCE and reuses it, passes
  `repo_id`/`device="cpu"`, sets `torch.set_num_threads(1)`, iterates the generator and concatenates
  chunks to a float32 1-D mono array, and exposes `sample_rate == 24000`.
- [x] T012 [US1] Implement `src/ankivoice/speech.py` (`Synthesizer` Protocol + `KokoroSynthesizer`,
  lazy single load, CPU-pinned) to pass T011.

### MP3 encoding (load-bearing, test-first)

- [x] T013 [P] [US1] Write FAILING `tests/unit/test_audio.py`: `encode_mp3` turns a short 24 kHz
  float32 mono array into a real `.mp3` on disk (validate via `ffprobe`/magic bytes: codec mp3, mono,
  ~expected duration); raises a clear error if ffmpeg is missing/fails.
- [x] T014 [US1] Implement `src/ankivoice/audio.py` (ffmpeg+libmp3lame via stdin pipe, per research.md)
  to pass T013.

### Packaging (load-bearing, test-first)

- [x] T015 [P] [US1] Write FAILING `tests/unit/test_packaging.py`: `build_apkg` builds a `.apkg` from
  `MediaCard`s + media paths; unzip it and assert `collection.anki2` + `media` JSON map + numbered
  media files; assert the answer template (`afmt`) contains the audio field and the question template
  (`qfmt`) does NOT (answer-only auto-play); deterministic `model_id`/`deck_id`; Note field-count guard;
  `output_name()` derives a safe name from the filename stem with the generic fallback.
- [x] T016 [US1] Implement `src/ankivoice/packaging.py` (genanki model/deck/note/package, `[sound:]` in
  `afmt`, bundled media, `output_name`) to pass T015.

### Core pipeline + end-to-end (load-bearing, test-first)

- [x] T017 [P] [US1] Write FAILING `tests/unit/test_pipeline.py`: `build_package(deck_bytes, synth,
  job_dir, …)` parses, synthesizes each UNIQUE `spoken` once (assert `FakeSynthesizer` call count =
  distinct sentences → dedupe by `sha256(spoken)`), encodes per-card MP3s, builds the `.apkg`; original
  text preserved; cleaned text used for audio; media count = usable cards.
- [x] T018 [US1] Implement `src/ankivoice/pipeline.py` (`build_package`, per-deck dedupe cache) to pass
  T017.
- [x] T019 [US1] Write `tests/integration/test_pipeline_e2e.py` (the Constitution-VII end-to-end test):
  `sample_deck.txt` → `build_package` with `FakeSynthesizer` → unzip the `.apkg` and assert it is
  importable (expected entries), one audio per usable card, skipped rows excluded, `afmt` sound tag
  present. Confirm RED against stubs, then GREEN.

**Checkpoint (MVP)**: deck file → importable audio `.apkg`; `uv run pytest` green and offline.

---

## Phase 4: User Story 2 - One-at-a-time fair durable queue (Priority: P2)

**Goal**: durable FCFS queue, exactly one synthesis at a time, queue-position replies, at most one
active job per user, delivery overlaps next synthesis, restart resumes pending work.

**Independent Test**: `test_store.py` + `test_worker.py` + `test_store_resume.py` +
`test_bot_handlers.py`: two near-simultaneous jobs run strictly in order with one synthesis at a time;
restart requeues in-progress jobs; handler replies queue position and declines a second active job.

### Durable job store (load-bearing, test-first)

- [x] T020 [P] [US2] Write FAILING `tests/unit/test_store.py`: schema init (WAL); `enqueue`;
  `has_active_job`; one-active-per-user guard (second enqueue refused); `claim_next` = smallest-id
  `QUEUED` → `SYNTHESIZING` (FCFS); `set_state`; `queue_position`; `get`; `list_active`.
- [x] T021 [US2] Implement `src/ankivoice/store.py` core to pass T020.
- [x] T022 [P] [US2] Write FAILING `tests/integration/test_store_resume.py`: reopening the DB persists
  jobs; `requeue_in_progress` resets `SYNTHESIZING/PACKAGING/UPLOADING/DELIVERED` → `QUEUED` and leaves
  `CLEANED/FAILED` terminal.
- [x] T023 [US2] Add `requeue_in_progress` + durable-reopen behavior to `src/ankivoice/store.py` to
  pass T022.

### Single worker (load-bearing, test-first)

- [x] T024 [P] [US2] Write FAILING `tests/unit/test_worker.py`: with `FakeSynthesizer` + `FakeSender` +
  a tmp store — worker claims FCFS, runs `pipeline.build_package` via `to_thread`, advances states; only
  ONE synthesis at a time (FakeSynthesizer asserts no overlap); a processing failure → `FAILED` +
  user notified + scoped clean; the next job's synthesis can start while a prior delivery task runs
  (overlap). Burst resilience (SC-007, FR-028): enqueue a burst of several jobs and assert all complete
  strictly FCFS one-at-a-time without error and the work dir returns to baseline (no accumulation).
- [x] T025 [US2] Implement `src/ankivoice/worker.py` (`Worker.run(stop)`, single loop, `to_thread`
  synthesis, dispatch delivery as a separate task) to pass T024.

### Telegram bot layer (test-first)

- [x] T026 [P] [US2] Write FAILING `tests/integration/test_bot_handlers.py` (PTB `Update`/`Bot` faked,
  no network): `on_document` rejects `file_size > MAX_FILE_BYTES` (TOO_LARGE message, no job); declines
  when `has_active_job` (friendly message, no job); otherwise saves the upload into a new `job_<id>/`
  dir, enqueues, and replies the queue position; `/start`/`/help` return usage text.
- [x] T027 [US2] Implement `src/ankivoice/bot.py` (`build_application`, document + command handlers,
  `TelegramSender` implementing `delivery.Sender`, worker wiring via `post_init`/`post_shutdown`) to
  pass T026.
- [x] T028 [US2] Implement `src/ankivoice/__main__.py` entrypoint: `load_config` →
  `JobStore(...).requeue_in_progress()` → build `KokoroSynthesizer` → `build_application` →
  `app.run_polling()`.

**Checkpoint**: durable queue + single worker + bot + restart-resume all green.

---

## Phase 5: User Story 3 - Archive backup, ready-notification & always-clean-up (Priority: P3)

**Goal**: deliver to archive first then user, send a friendly "ready" message, and only after BOTH
uploads succeed remove the job's files; cleanup is scoped strictly to the job dir; disk stays flat;
undelivered packages are retained for resume.

**Independent Test**: `test_delivery.py` + `test_cleanup.py`: archive precedes user; cleanup runs only
after both succeed; on upload failure files are retained; deletion outside `WORK_DIR` is refused.

### Scoped cleanup (load-bearing, test-first)

- [x] T029 [P] [US3] Write FAILING `tests/unit/test_cleanup.py`: `remove_job_dir` deletes a dir inside
  `work_root`; RAISES for a target outside `work_root`; refuses a symlink that escapes `work_root`;
  is idempotent when the dir is already gone.
- [x] T030 [US3] Implement `src/ankivoice/cleanup.py` (`remove_job_dir`, resolved-path scope assertion)
  to pass T029.

### Delivery + cleanup orchestration (load-bearing, test-first)

- [x] T031 [P] [US3] Write FAILING `tests/unit/test_delivery.py`: `deliver` sends to `archive_chat_id`
  BEFORE the user (assert order via `FakeSender`), then a "ready" message; only after BOTH succeed →
  `set_state(DELIVERED)` → `remove_job_dir` → `set_state(CLEANED)`; if the archive send fails → not
  delivered, job dir RETAINED, state not `CLEANED`; if the user send fails after archive → retained
  (resume-safe); never deletes outside the job dir. Privacy (FR-029): assert `FakeSender` received
  documents ONLY for the archive chat and the requesting user — no other destination.
- [x] T032 [US3] Implement `src/ankivoice/delivery.py` (`Sender` Protocol + `deliver`) to pass T031.
- [x] T033 [US3] Wire delivery into `src/ankivoice/worker.py` (dispatch `deliver(...)` as a separate
  task → overlaps next synthesis; on terminal processing failure call `remove_job_dir`) and extend
  `tests/unit/test_worker.py` to assert clean-after-both-uploads and disk returns to baseline.

**Checkpoint**: archive-first delivery + ready message + scoped cleanup; flat disk verified.

---

## Phase 6: User Story 4 - Clear, friendly, actionable errors (Priority: P3)

**Goal**: every bad input (wrong format, empty/zero usable, too large, too many cards) gets a specific
friendly message; the service stays healthy; no residual files.

**Independent Test**: `test_errors_e2e.py`: each bad input yields its specific message via the
bot/worker path, the store stays consistent, and no job dir remains.

- [ ] T034 [P] [US4] Write FAILING `tests/integration/test_errors_e2e.py` (faked PTB + FakeSynthesizer):
  a wrong-format file → `WRONG_FORMAT` message + no residual files; empty/zero-usable → `EMPTY`
  message; too-many-cards → `TOO_MANY_CARDS` message; oversized upload → `TOO_LARGE` at the handler;
  after each, the service is still healthy and the job dir is gone.
- [ ] T035 [US4] Complete friendly-error surfacing: `worker.py` catches `ValidationError` → sends
  `err.user_message`, sets `FAILED`, scoped-cleans; `bot.py` emits the `TOO_LARGE`/active-job messages.
  Make T034 green.

**Checkpoint**: all four stories independently testable and green.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T036 [P] Create `.env.example` documenting every `ANKIVOICE_*` key with safe placeholder values
  (no secrets).
- [ ] T037 [P] Create `scripts/warmup.py`: one-time online download of Kokoro weights + the default
  voice pack + the spaCy `en_core_web_sm` model; prints the cache location and how to enable
  `HF_HUB_OFFLINE=1`.
- [ ] T038 [P] Write `README.md`: quickstart, env-var table, system deps (ffmpeg, espeak-ng), how to
  run (`uv run python -m ankivoice`), how to run tests (default + `-m live`), the module map, and the
  manual test plan from the brief's handoff.
- [ ] T039 [P] Write `tests/live/test_live_kokoro_apkg.py`: `@pytest.mark.live`, self-skipping (skip if
  the model/voice are not cached or ffmpeg/espeak-ng are missing) — real `KokoroSynthesizer` → real MP3
  → `build_apkg` → reopen and validate the `.apkg` end-to-end. Kept out of the default run.
- [ ] T040 Run the full default suite (`uv run pytest`) to green; run quickstart.md validation; confirm
  every load-bearing path has a test and `CLAUDE.md`/README are accurate.

---

## Dependencies & Execution Order

- **Setup (P1)** → **Foundational (P2)** block everything.
- **US1 (P3 phase, MVP)** depends only on Foundational. It is the standalone MVP.
- **US2** depends on Foundational + US1's `pipeline.build_package` (the worker calls it).
- **US3** depends on US2 (delivery is wired into the worker; cleanup is independent but tested with it).
- **US4** depends on US1 (parser errors) + US2 (worker/bot surfacing).
- **Polish** depends on all desired stories; T039 (live) and T036–T038 are independent `[P]`.

### Within each story (TDD)

The `…(failing)` test task is RED before its implementation task. Models before services; services
before the bot layer; core before integration. Commit after each green task.

## Parallel Opportunities

- Setup: T003, T004 in parallel.
- Foundational: T005 and T007 (different files) in parallel.
- US1: the failing-test tasks T009, T011, T013, T015, T017 touch different files and can be authored in
  parallel; each implementation task follows its own test.
- US2: T020, T024, T026 (different test files) can be authored in parallel.
- US3: T029, T031 in parallel.
- Polish: T036, T037, T038, T039 in parallel.

## Implementation Strategy

1. **MVP = US1**: Setup → Foundational → US1 → STOP & validate `test_pipeline_e2e.py` (deck → playable
   `.apkg`). This alone delivers the product's core value.
2. **Incremental**: add US2 (queue/worker/bot) → US3 (archive+cleanup) → US4 (friendly errors). Each
   layer keeps the pipeline test green (Constitution P3).
3. Run `tests/live/test_live_kokoro_apkg.py` (`-m live`) once before handoff to validate the real
   engines end-to-end.

## Notes

- Tests fake Kokoro (mock `KPipeline` / `FakeSynthesizer`) and Telegram (`FakeSender`, faked
  `Update`/`Bot`) so the default suite is fast + offline (Constitution P7).
- All DB access on the event-loop thread; CPU synthesis via `asyncio.to_thread`; `torch.set_num_threads(1)`.
- Cleanup is always scoped to the job dir (`remove_job_dir`); never deletes outside `WORK_DIR`
  (Constitution P5). Commit after each task or logical group.
