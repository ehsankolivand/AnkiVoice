# Quickstart & Validation: cycle 002 increment

Proves the increment's user stories. Prereqs and the base run/test commands are unchanged from
[001 quickstart](../001-ankivoice-audio-decks/quickstart.md). All default validation is fast + offline.

```bash
uv run pytest            # full default suite (all 002 regression tests + existing) — must be green
uv run pytest -m live    # opt-in real Kokoro + real .apkg (self-skips if model/tools absent)
```

## Validate each story

1. **US1 — no silent loss / faithful display** (IR-001..007): `tests/unit/test_parser.py` (new cases:
   leading-quote answer keeps quotes; unbalanced quote does NOT swallow following rows; BOM file skips
   headers; balanced transport quote unwrapped; blank-after-clean skipped) and
   `tests/unit/test_packaging.py` / `test_pipeline.py` (empty-Front → studyable card; identical rows →
   2 cards; full-digest filename). Manual: import a deck whose answer begins with `"` → the card shows
   the quotes; a BOM-saved export imports without a junk header card.
2. **US2 — fail-fast startup** (IR-008..011): `tests/unit/test_preflight.py` — with `which` mocked to
   miss espeak-ng / ffmpeg, or the voice probe failing, `check_runtime` raises `PreflightError` naming
   it; with all present it returns and the model is warm. Manual: rename `espeak-ng` off PATH →
   `python -m ankivoice` exits immediately with a specific message.
3. **US3 — flat disk & bounded datastore** (IR-012,013): `tests/unit/test_packaging.py` leak test (no
   temp file outside the job dir after a build+clean) and `tests/unit/test_store.py` prune test
   (terminal rows capped at `ANKIVOICE_JOB_HISTORY`).
4. **US4 — exactly-once delivery + bounded retry** (IR-014,015): `tests/unit/test_delivery.py` (re-run
   after archive-sent sends only the user copy; fully-delivered re-run sends nothing) and
   `tests/unit/test_worker.py` (bounded retry then retain-for-resume; resume re-sends only the missing
   copy).
5. **US5 — safe speedup** (IR-016..018): `perf-notes.md` records before/after on the representative deck
   (build time unchanged within noise — synthesis is model-bound; the engine is non-deterministic per
   call so byte-equality is not asserted); `test_audio.py` (encode timeout + memoized path);
   `test_speech_wrapper.py` (inference_mode); `test_pipeline.py` (dedupe preserved; full-digest filename).
6. **US6 — one consistent story** (IR-019,020): `uv run` the `/speckit-analyze` consistency pass → zero
   contradictions; spot-check that 001 spec/plan/data-model/research/contracts/tasks and `CLAUDE.md`
   describe the reconciled behaviours identically.

## Expected outcomes

- `uv run pytest` → all green in seconds, offline; new regression tests pin every fix.
- A leading-quote / BOM / duplicate-row / empty-prompt deck all convert correctly.
- Misconfigured host → service refuses to start with a specific message.
- After N decks: no temp files outside job dirs; ≤ `ANKIVOICE_JOB_HISTORY` terminal rows; disk flat.
- No duplicate deliveries across a mid-delivery restart.
- Representative-deck time ≤ baseline within noise (synthesis is model-bound); audio-generation path
  unchanged (engine non-deterministic per call, so byte-equality is not measured).
