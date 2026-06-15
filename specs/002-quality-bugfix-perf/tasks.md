---
description: "Task list for AnkiVoice cycle 002 — quality / bug-fix / performance (test-first / TDD)"
---

# Tasks: AnkiVoice — Quality, Bug-Fix & Performance Increment

**Input**: Design documents from `specs/002-quality-bugfix-perf/` (spec, plan, research, data-model,
contracts/changes.md, audit-notes.md, perf-notes.md).

**Tests**: REQUIRED and TEST-FIRST for every fix and reconciled behavior (Constitution VII,
NON-NEGOTIABLE; brief: strict TDD). Each `…(failing)` test task is confirmed RED before the
implementation task immediately following it. The default suite stays fast + fully offline (FakeSynthesizer
+ FakeSender); the one `@pytest.mark.live` test stays self-skipping. No existing test is weakened/deleted.

**Organization**: by user story (US1..US6 from spec.md), in priority order. Each task names exact paths.
Every fix traces to a finding ID in [audit-notes.md](./audit-notes.md) and a decision in
[research.md](./research.md).

## Format: `[ID] [P?] [Story] Description`
- **[P]**: parallelizable (different files, no incomplete dependency).
- Commit after each green task (on branch `002-quality-bugfix-perf`; main untouched).

---

## Phase 1: Setup

- [x] T001 Confirm baseline: `uv run pytest` green (82 passed, 1 deselected) and record the
  representative-deck baseline timing (already in `specs/002-quality-bugfix-perf/perf-notes.md`).
- [x] T002 [P] Add committed representative fixture `tests/fixtures/representative_deck.txt` (≥50 full
  English-sentence cards incl. ~10 duplicate rows) for the perf/dedupe assertions.

## Phase 2: Foundational (shared types + store; blocks US1/US3/US4/US6) ⚠️

- [x] T003 Write FAILING `tests/unit/test_models_errors.py` updates: `JobState` has exactly
  {queued,synthesizing,uploading,delivered,cleaned,failed} (NO `packaging`); `Job` carries
  `archive_sent`/`user_sent` booleans. (research D7/D8)
- [x] T004 Implement `src/ankivoice/models.py`: remove `JobState.PACKAGING`; add `Job.archive_sent`,
  `Job.user_sent` (default False). Make T003 green.
- [x] T005 Write FAILING `tests/unit/test_store.py` + `tests/integration/test_store_resume.py` updates:
  additive schema migration adds `archive_sent`/`user_sent` (reopen old DB safe); `_ACTIVE`/`_AHEAD`/
  `_REBUILDABLE` contain no PACKAGING (`_AHEAD={queued,synthesizing}`); `enqueue_if_no_active` returns a
  Job when free and `None` when the user already has an active job (atomic); `set_delivery_flag`;
  `prune_terminal_jobs(keep=N)` keeps the N most-recent terminal rows and never prunes active jobs;
  `requeue_in_progress` resets {synthesizing,uploading}+legacy `'packaging'`→queued WITHOUT resetting the
  delivery flags and does NOT touch DELIVERED. (research D7/D8/D9/D10)
- [x] T006 Implement `src/ankivoice/store.py`: schema + migration; updated state sets;
  `enqueue_if_no_active`; `set_delivery_flag`; `prune_terminal_jobs`; legacy-`packaging`-aware
  `requeue_in_progress`; `_row_to_job` reads the new columns. Make T005 green.
- [x] T006b Write FAILING `tests/unit/test_config.py` cases then implement `src/ankivoice/config.py`:
  add `job_history` (default 500), `ffmpeg_timeout` (default 120), `delivery_retries` (default 3) read
  from `ANKIVOICE_*` env. (Foundational because US3/US4/US5 all consume these; resolves analyze C1.)

**Checkpoint**: shared types + durable store + config ready (corrected state machine + delivery flags).

---

## Phase 3: User Story 1 — No silent loss; faithful display (Priority: P1) 🎯

**Goal**: one correct card per usable row; displayed text = normal-import value; correct audio.
**Independent test**: `tests/unit/test_parser.py`, `test_pipeline.py`, `test_packaging.py`,
`test_pipeline_e2e.py` green with the new edge cases.

- [x] T007 [P] [US1] Write FAILING `tests/unit/test_parser.py` cases (audit A1/A2/A4, research D1/D2/D3):
  (a) a Back beginning with an unbalanced quote does NOT swallow following rows (3 rows → 3 cards);
  (b) a Back beginning with a literal quote keeps its quotes in `back` (display) byte-for-byte;
  (c) a genuine balanced transport-quoted field is unwrapped + inner `""`→`"` for both display & spoken
  (keep existing `test_csv_quote_wrapping_unwrapped_for_both`); (d) a BOM-prefixed file with `#` headers
  skips the headers and yields no junk card; (e) a Back that cleans to whitespace (`&#32;`/`&nbsp;`) is
  skipped+counted; (f) `clean_for_speech` does balanced-unwrap + html.unescape.
- [x] T008 [US1] Implement `src/ankivoice/parser.py`: `utf-8-sig` decode; line-by-line first-TAB split
  (no row merging); `_unwrap_balanced` helper (balanced-only); `back`/`front` display = unwrap-if-balanced;
  `spoken = html.unescape(_unwrap_balanced(back))`; skip+count rows whose `spoken.strip()==""`;
  `clean_for_speech = html.unescape(_unwrap_balanced(field))`. Make T007 green.
- [x] T009 [P] [US1] Write FAILING `tests/unit/test_pipeline.py` case (audit A3/D4): per-card MP3
  filename equals `sha256(spoken).hexdigest()+".mp3"` (FULL digest); dedupe still keys on full spoken
  (identical sentences → one MP3). Keep existing dedupe/fidelity tests.
- [x] T010 [US1] Implement `src/ankivoice/pipeline.py`: full-digest filename. Make T009 green.
- [x] T011 [P] [US1] Write FAILING `tests/unit/test_packaging.py` case (audit E1): `build_apkg` raises a
  clear error if a card's `audio_filename` basename has no matching media path (keep empty-front and
  identical-rows tests).
- [x] T012 [US1] Implement `src/ankivoice/packaging.py` basename↔media assertion. Make T011 green.
- [x] T013 [US1] Update `tests/integration/test_pipeline_e2e.py` to assert the reconciled fidelity
  (6 usable cards; entities kept; balanced transport quotes unwrapped; line endings normalized) and keep
  it green.

**Checkpoint**: edge-case decks convert losslessly and faithfully.

---

## Phase 4: User Story 2 — Fail-fast startup guard (Priority: P1)

**Goal**: refuse to start when it cannot produce correct audio. **Independent test**:
`tests/unit/test_preflight.py` + `tests/integration/test_main.py`.

- [x] T014 [US2] Write FAILING `tests/unit/test_preflight.py` (audit C1, research D11; self-review #0):
  miss ffmpeg → `PreflightError` naming ffmpeg; espeak-ng NOT on PATH is FINE (it is bundled via
  espeakng_loader — must not refuse); probe synth raising (broken phonemizer / uncached voice) →
  `PreflightError` naming the voice + warm-up; all present → returns None and the probe synth ran once
  (prewarm); `ANKIVOICE_SKIP_PREFLIGHT` set → no checks run.
- [x] T015 [US2] Implement `src/ankivoice/preflight.py` (`PreflightError`,
  `check_runtime(config, synthesizer)`: ffmpeg on PATH; a one-word OUT-OF-DICTIONARY probe synth with the
  configured voice to verify the phonemizer/voice/model + prewarm; NO espeak-ng PATH gate (bundled);
  honor `ANKIVOICE_SKIP_PREFLIGHT`). Make T014 green.
- [x] T016 [US2] Update FAILING `tests/integration/test_main.py`: `main()` calls
  `preflight.check_runtime` before `run_polling` (assert order via patches) and reuses the prewarmed
  synthesizer. Then implement the wiring in `src/ankivoice/__main__.py`. Green.

**Checkpoint**: misconfigured host refuses to start with a specific message; model warm before job 1.

---

## Phase 5: User Story 3 — Flat disk & bounded datastore (Priority: P2)

**Goal**: no engine temp leak; bounded job table. **Independent test**: `test_packaging.py` leak test +
`test_store.py`/`test_worker.py` prune.

- [x] T017 [P] [US3] Write FAILING `tests/unit/test_packaging.py` leak test (audit B1, research D6): with
  `TMPDIR` isolated, build a package into a job dir, remove the job dir, assert ZERO leftover temp files
  remain in the isolated system temp dir.
- [x] T018 [US3] Implement `src/ankivoice/packaging.py`: scope `tempfile.tempdir` to `out_path.parent`
  around `write_to_file` (save/restore in `finally`). Make T017 green.
- [x] T019 [US3] Write FAILING `tests/unit/test_worker.py` resume-prune test: `resume()` calls
  `prune_terminal_jobs(keep=config.job_history)` so terminal rows are bounded; active jobs untouched.
- [x] T020 [US3] Implement the prune call in `src/ankivoice/worker.py` `resume()` using
  `config.job_history` (added in T006b). Make T019 green.

**Checkpoint**: disk strictly flat (incl. engine temp); datastore bounded.

---

## Phase 6: User Story 4 — Exactly-once delivery + bounded retry (Priority: P2)

**Goal**: no duplicate delivery across restart; bounded retry. **Independent test**: `test_delivery.py`,
`test_worker.py`, `test_bot_handlers.py`.

- [x] T021 [P] [US4] Write FAILING `tests/unit/test_delivery.py` (audit D1, research D8): `deliver`
  sets `archive_sent` after the archive send and `user_sent` after the user send; a re-run when
  `archive_sent` is already true sends ONLY the user copy (archive not re-sent); a re-run when both flags
  set sends NOTHING and just cleans; archive-first order + privacy (only archive+user) preserved.
- [x] T022 [US4] Implement `src/ankivoice/delivery.py`: per-copy idempotent send + flag setting; mark
  DELIVERED once both set; ready message (best-effort) after DELIVERED, then scoped cleanup. Make T021
  green.
- [x] T023 [P] [US4] Write FAILING `tests/unit/test_worker.py` cases: a transient delivery failure is
  retried up to `config.delivery_retries` with backoff then retained (not deleted) for resume; on resume,
  an `uploading` job with `archive_sent=True` re-delivers ONLY the user copy (exactly-once).
- [x] T024 [US4] Implement `src/ankivoice/worker.py`: set `UPLOADING` (not PACKAGING) synchronously after
  build; bounded delivery retry with backoff in `_deliver`; resume rebuilds uploading jobs (flags
  intact). Make T023 green.
- [x] T025 [P] [US4] Write FAILING `tests/integration/test_bot_handlers.py` case (audit D2, research D9):
  `on_document` uses `enqueue_if_no_active`; when refused after a save, the orphan job dir is removed and
  the user is told a deck is already processing.
- [x] T026 [US4] Implement `src/ankivoice/bot.py`: `enqueue_if_no_active` + orphan cleanup on refusal.
  Make T025 green.

**Checkpoint**: delivery is exactly-once across restarts; transient failures retried, then deferred.

---

## Phase 7: User Story 5 — Safe performance (Priority: P3)

**Goal**: same-or-faster, audio-generation computation unchanged (engine non-deterministic per call →
byte-equality not asserted), invariants intact. **Independent test**: `test_audio.py`, `test_pipeline.py`,
`test_speech_wrapper.py`, perf re-measure.

- [x] T027 [P] [US5] Write FAILING `tests/unit/test_audio.py` cases (audit A5/#15, research D5): `encode_mp3`
  accepts/uses a `timeout` and raises a clear error on `TimeoutExpired` (patched); the ffmpeg path is
  resolved at most once across multiple encodes (memoized).
- [x] T028 [US5] Implement `src/ankivoice/audio.py`: memoized ffmpeg path + `subprocess.run(timeout=...)`
  with a clear `RuntimeError` on timeout. Make T027 green.
- [x] T029 [US5] Implement `torch.inference_mode()` wrap in `src/ankivoice/speech.py.synthesize`
  (computation unchanged — engine non-deterministic per call; `tests/unit/test_speech_wrapper.py` pins it).
- [x] T030 [US5] Re-measure the representative deck (real Kokoro, single core, offline) and record the
  AFTER numbers in `specs/002-quality-bugfix-perf/perf-notes.md` (build time model-bound/unchanged within
  noise; engine non-deterministic so byte-equality is not asserted).

**Checkpoint**: safe speedups applied; numbers recorded; no invariant weakened.

---

## Phase 8: User Story 6 — One consistent story (reconcile docs) (Priority: P3)

**Goal**: every 001 artifact + CLAUDE.md matches the corrected code (audit §G map). [P] = different file.

- [x] T031 [P] [US6] Reconcile `specs/001-ankivoice-audio-decks/spec.md`: FR-003 (first-TAB), FR-011
  (clean_for_speech = balanced-unwrap + entity-decode), FR-012/SC-003 (fidelity = decoded field;
  transport quotes unwrapped for balanced fields, line endings→LF, BOM stripped); add edge cases (BOM,
  leading/unbalanced quote, blank-after-clean skip, empty-Front placeholder, line endings). Add a brief
  reference to the 002 increment + the startup guard.
- [x] T032 [P] [US6] Reconcile `specs/001-ankivoice-audio-decks/data-model.md`: `skipped_empty_back`
  meaning (counts no-TAB + blank-after-clean); JobState without PACKAGING; resume (DELIVERED cleaned not
  requeued; per-copy flags; rebuildable set); queue position (`_AHEAD`); Job gains delivery flags; new
  config keys.
- [x] T033 [P] [US6] Reconcile `specs/001-ankivoice-audio-decks/research.md`: guid scheme (deck stem +
  index + content); line-ending normalization; BOM strip; HF offline env + `ANKIVOICE_ALLOW_DOWNLOADS`;
  espeak/ffmpeg startup guard; inference_mode; ffmpeg timeout; cross-job cache rejected (constitution);
  PACKAGING removed.
- [x] T034 [P] [US6] Reconcile `specs/001-ankivoice-audio-decks/contracts/module-interfaces.md`:
  clean_for_speech + parse_deck wording; `build_application(config, store, synthesizer)`; `__main__.main`;
  delivery ready-message ordering; JobState; new store methods (enqueue_if_no_active, set_delivery_flag,
  prune_terminal_jobs); packaging basename assertion + temp scoping; add `preflight` interface.
- [x] T035 [P] [US6] Reconcile `specs/001-ankivoice-audio-decks/contracts/bot-interface.md` (orphan
  cleanup on refused second active job) and `specs/001-ankivoice-audio-decks/tasks.md` (T022/T023 DELIVERED
  not requeued; T009 clean_for_speech; T015 field-count guard + media-count = distinct spoken).
- [x] T036 [P] [US6] Reconcile `specs/001-ankivoice-audio-decks/plan.md` (module count 12→14; add
  pipeline + preflight; note guard/inference_mode/temp-scoping) and
  `specs/001-ankivoice-audio-decks/checklists/requirements.md` (FR count 30→31).
- [x] T037 [P] [US6] Update `.env.example` (new keys: `ANKIVOICE_JOB_HISTORY`, `ANKIVOICE_FFMPEG_TIMEOUT`,
  `ANKIVOICE_DELIVERY_RETRIES`, `ANKIVOICE_SKIP_PREFLIGHT`; document `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`/
  `ANKIVOICE_ALLOW_DOWNLOADS`/`ANKIVOICE_MODEL_DIR`) and `README.md` (module map +preflight; startup guard;
  new keys). CLAUDE.md managed block already refreshed.

**Checkpoint**: `/speckit-analyze` reports zero contradictions across artifacts and code.

---

## Phase 9: Polish & Verification

- [x] T038 [P] Add `tests/unit/test_packaging.py` field-count guard test (genanki raises on a note whose
  field count ≠ model field count) to make 001 tasks.md T015 accurate.
- [x] T039 Run the full default suite `uv run pytest` → GREEN; confirm fast + offline.
- [x] T040 Run `uv run pytest -m live` → the real Kokoro + real `.apkg` test passes (model is cached).
- [x] T041 Final adversarial self-review (subagents) over: parser fidelity/no-loss, BOM, startup guard,
  exactly-once delivery + resume, genanki temp leak, prune, perf (computation unchanged + numbers), .apkg
  correctness; fix anything confirmed; re-run the suite.

---

## Dependencies & Execution Order

- Setup (P1) → Foundational (P2: models+store) block US1/US3/US4/US6.
- US1 depends on Foundational. US2 (preflight) depends only on Foundational/config. US3 depends on
  Foundational (prune) + packaging. US4 depends on Foundational (flags/state) + delivery/worker/bot.
  US5 is independent (audio/speech). US6 (docs) depends on all behavior being final.
- Within each story: failing test → minimal implementation → refactor; commit after each green task.

## Parallel Opportunities

- Foundational: T003 and T005 are authored in different files (then their impls).
- US1: T007/T009/T011 (different test files) in parallel.
- US4: T021/T023/T025 (different test files) in parallel.
- US6: T031–T037 are all different files → fully parallel.

## Implementation Strategy

1. Foundational first (state machine + store) so US1/US4 build on it.
2. US1 (lossless/faithful) is the highest-value correctness slice — land it first.
3. US2 (guard), US3 (flat disk), US4 (exactly-once) next; US5 (perf) and US6 (docs) close it out.
4. Keep the e2e pipeline test green throughout (Constitution III). Reconcile docs in lockstep with code
   so no artifact is ever left contradicting the implementation.
