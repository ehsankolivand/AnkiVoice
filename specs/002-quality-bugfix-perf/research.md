# Research & Decisions: AnkiVoice cycle 002 (quality / bug-fix / performance)

Each decision below is verified against the installed packages and the measured profile, and traces to a
confirmed finding in [audit-notes.md](./audit-notes.md). Format: Decision / Rationale / Alternatives
rejected. These pin the implementation (Constitution: verify-don't-guess).

## D1 — Parser fidelity: line-by-line first-TAB split + balanced-only unwrap (audit A1, G3/G4/G10)

**Decision**: Replace the whole-body `csv.reader(StringIO(body), quotechar='"')` with: decode
`utf-8-sig`; normalize line endings to `\n`; skip the leading contiguous `#`-header block; iterate
**line by line**; split each data line on the **first TAB** into `front, back`; for both display and
spoken, unwrap a field **only when it is a complete balanced transport-quoted field** (starts and ends
with `"`, with internal `""` un-doubled to `"`) via a small `_unwrap_balanced(field)` helper; otherwise
keep the field's characters exactly. `spoken = html.unescape(_unwrap_balanced(back))`;
`back(display) = _unwrap_balanced(back)`. `clean_for_speech(field)` becomes
`html.unescape(_unwrap_balanced(field))` — now matching its contract.

**Rationale**: Line-by-line splitting makes it **impossible** to merge rows (guarantees FR-008 "one card
per usable row"), eliminating the silent row-swallowing from an unbalanced leading quote. Balanced-only
unwrap still strips genuine Anki transport quoting (exports escape literal quotes as `""`, so a quoted
field is always balanced) → FR-011 preserved and the existing
`test_csv_quote_wrapping_unwrapped_for_both` still passes. Literal leading quotes in hand-edited files
are preserved byte-for-byte → FR-012. This also makes the code match FR-003 / the contract ("split on
the first TAB") and makes `clean_for_speech` actually perform the unwrap its contract claims.

**Accepted limitation (self-review #6):** because each line is split on TAB into fields, a field that
*contains* a literal TAB inside balanced quotes (which a strict CSV reader would keep as one field) is
split mid-field. This is rare in Anki "Notes in Plain Text" exports (fields with a separator TAB are
uncommon) and is the deliberate cost of guaranteeing one-card-per-row (no silent row loss). Documented
here and in the spec edge cases rather than re-introducing whole-body CSV parsing.

**Alternatives rejected**: keep `csv.reader` (causes silent row loss — the original bug);
`csv.QUOTE_NONE` over the body (would never unwrap genuine transport quotes, breaking FR-011);
preserving multi-line/quoted-TAB fields by spanning lines / strict CSV (the rare gain is not worth
re-introducing the catastrophic row-merge; Anki "Notes in Plain Text" uses HTML `<br>`, not raw newlines
or literal tabs, inside fields).

## D2 — UTF-8 BOM stripping (audit A2)

**Decision**: decode with `raw.decode("utf-8-sig")` (was `"utf-8"`).

**Rationale**: A leading BOM is transport encoding, not content. `utf-8-sig` strips exactly one leading
BOM if present, is a no-op otherwise, and still raises `UnicodeDecodeError` on truly non-UTF-8 bytes →
`WRONG_FORMAT` is preserved. Fixes header lines leaking in as junk cards on Windows exports.

**Alternatives rejected**: `text.lstrip("﻿")` (would also strip a legitimate interior ZWNBSP in
the first field; less precise).

## D3 — Blank-spoken rows are not usable (audit A4)

**Decision**: after computing `spoken`, treat a card whose `spoken.strip() == ""` as a skipped
no-usable-answer row (increment `skipped_empty_back`), the same as an empty Back.

**Rationale**: A Back of only `&#32;`/`&nbsp;`/markup cleans to whitespace → silent/blank audio. FR-008
intent is "cannot be voiced → skip and count." Keeps the usable-card invariant honest.

**Alternatives rejected**: voice the blank (produces a useless silent card); reject the whole deck
(too aggressive — other rows may be fine).

## D4 — MP3 filename uses the full digest (audit A3, perf F3)

**Decision**: name per-sentence MP3s `sha256(spoken).hexdigest()` + `.mp3` (full 64-hex), not the
16-hex prefix; dedupe still keys on the full spoken string.

**Rationale**: The 16-hex (64-bit) prefix could collide across two distinct sentences → one MP3
overwrites the other → wrong audio on a card. Full digest removes the latent collision at zero cost
(filenames remain well within filesystem limits).

**Alternatives rejected**: key the dedupe cache on the truncated digest too (would make distinct
sentences share audio — worse).

## D5 — ffmpeg encode: memoized path + bounded timeout (audit A5, perf F2)

**Decision**: resolve `shutil.which("ffmpeg")` once (module-level memo, re-checkable) instead of per
call; pass a generous `timeout=` to `subprocess.run` and raise a clear `RuntimeError` on
`TimeoutExpired` (default `ANKIVOICE_FFMPEG_TIMEOUT` seconds).

**Rationale**: Removes a redundant PATH scan per unique sentence (perf, removes redundant work) and
prevents a stuck encoder from hanging the single worker (Constitution I). The startup guard already
asserts ffmpeg exists, so the memo is populated at first use.

**Alternatives rejected**: no timeout (a hung subprocess freezes all processing — unacceptable on a
single worker).

## D6 — genanki system-temp leak: scope the temp into the job dir (audit B1)

**Decision**: in `build_apkg`, set `tempfile.tempdir` to the output file's parent (the job dir) for the
duration of `package.write_to_file(...)`, restoring it in a `finally`. genanki's `write_to_file` uses
`tempfile.mkstemp()` and never removes the temp DB, so directing it into the job dir means scoped
cleanup (`remove_job_dir`) removes it.

**Rationale**: genanki (0.13.1, `package.py:30`) leaks a ~52 KB SQLite temp into the *system* temp dir
on every build — outside WORK_DIR → unbounded growth → breaks P5/FR-024 flat-disk. Builds are serialized
(one synthesis/packaging at a time in the worker thread), so the brief global-`tempfile.tempdir` window
is safe; we save/restore to avoid leaking the override.

**Alternatives rejected**: patch genanki (don't modify a dependency); identify-and-delete the temp by
scanning (fragile, racy); leave it (violates flat-disk — the whole reason the service survives on 40 GB).

## D7 — Remove the dead PACKAGING state (audit D3 / #24,#36,#45)

**Decision**: remove `JobState.PACKAGING`. The worker sets `UPLOADING` **synchronously** right after
`build_package` returns (preserving "at most one SYNTHESIZING"), then dispatches delivery.
`_REBUILDABLE = (SYNTHESIZING, UPLOADING)`, `_ACTIVE = (QUEUED, SYNTHESIZING, UPLOADING, DELIVERED)`,
`_AHEAD = (QUEUED, SYNTHESIZING)`. `resume()` maps any legacy `'packaging'` row to rebuildable so an
old DB is safe.

**Rationale**: Synthesis and packaging run inside one `to_thread` call, so PACKAGING was only ever set
*after* packaging finished — it never marked packaging work, only "left SYNTHESIZING before the next
claim," which UPLOADING does correctly. Removing it is the simplest honest model (Constitution
simplicity) and fixes queue-position (an uploading/delivering job is no longer counted "ahead").

**Alternatives rejected**: split build into two observable states (adds a callback/second to_thread for
a sub-second step — needless complexity); keep PACKAGING as a misnomer (documents a fiction).

## D8 — Exactly-once delivery via per-copy flags + bounded retry (audit D1, D5; brief findings A/M)

**Decision**: add durable `archive_sent` / `user_sent` booleans to the `jobs` row. `deliver()` sends a
copy only if its flag is false, sets the flag immediately after each successful send, marks `DELIVERED`
once both are set, then cleans. On resume, rebuildable jobs (incl. `UPLOADING`) are rebuilt and
`deliver()` re-runs — sending only the un-sent copy. The worker wraps each delivery attempt in a bounded
retry (`ANKIVOICE_DELIVERY_RETRIES` attempts with exponential backoff) before leaving the job for
restart.

**Rationale**: Makes delivery idempotent → a mid-delivery crash never double-sends (removes the
documented "rare duplicate to user/archive"). Bounded retry recovers transient failures without holding
the slot until restart, and without an unbounded loop (Constitution I). The flags are reset to false
only at enqueue (never by requeue), so a rebuilt job remembers what already went out.

**Alternatives rejected**: unbounded retry sweep (the 001 known-limitation — risks re-delivering
mid-delivery jobs and never terminates); content-addressed idempotency keys at the transport (Telegram
has no dedupe primitive we can rely on).

## D9 — Atomic one-active-per-user enqueue (audit D2; brief finding B)

**Decision**: add `store.enqueue_if_no_active(...)` performing the active-check and insert in one
transaction (a `BEGIN IMMEDIATE` guarded conditional insert), returning `None` if the user already has
an active job. `bot.on_document` calls it FIRST (before creating any job dir or downloading); on `None`
it replies immediately and returns — nothing is created, so there is no orphan to delete. A *download*
failure is handled by a separate branch that scoped-cleans the reserved dir and asks the user to resend.

**Rationale**: Today correctness relies on PTB's `max_concurrent_updates=1`; the store itself never
refuses despite data-model.md claiming it does. The atomic method makes the invariant hold in the store
(belt-and-suspenders, future-proof) and matches the contract. Because the slot is reserved *before* the
download, a refusal never leaves an orphaned file.

**Alternatives rejected**: rely on the event loop only (fragile to any future threading); a unique
partial index on `(user_id) WHERE state in active` (SQLite expression-index support is fiddly; an
explicit transaction is clearer and testable).

## D10 — Bounded job-table prune (audit D4; brief finding J)

**Decision**: add `store.prune_terminal_jobs(keep=N)` deleting all but the most recent N terminal
(`CLEANED`/`FAILED`) rows by id; call it from `worker.resume()` (startup). Default
`ANKIVOICE_JOB_HISTORY=500`.

**Rationale**: Terminal rows accrue forever today; WORK_DIR stays flat but the DB grows. A bounded
keep-recent prune keeps the datastore bounded while retaining recent observability. Active jobs are never
pruned.

**Alternatives rejected**: time-based TTL (clock-dependent, harder to test deterministically); never
prune (unbounded growth — the finding).

## D11 — Fail-fast startup guard `preflight.py` (audit C1; brief finding C, <correctness_guards>)

**Decision**: new `preflight.check_runtime(config, synthesizer)` that raises a clear `PreflightError`
if: `shutil.which("ffmpeg")` is None; or the configured voice/model + phonemizer cannot synthesize a
one-word **out-of-dictionary** probe offline. The probe both verifies availability AND prewarms the
model; under `ANKIVOICE_ALLOW_DOWNLOADS` it may download. `__main__.main()` calls it after building the
synthesizer and before `run_polling()`. `ANKIVOICE_SKIP_PREFLIGHT` is the tests/dev escape hatch.

**IMPORTANT correction (self-review #0, verified):** misaki loads `espeak-ng` from a **bundled** shared
library via the `espeakng_loader` Python dependency (`EspeakWrapper.set_library(...)`), NOT from a PATH
binary — synthesis of out-of-dictionary words works with no `espeak-ng` on PATH. So the guard does NOT
gate on `shutil.which("espeak-ng")` (that would be a false-positive refusing a working host). Instead the
**probe synthesis** (with an out-of-dictionary token, exercising misaki's espeak fallback) is the
ground-truth check: a broken/missing bundled phonemizer surfaces as the probe raising → `PreflightError`.

**Rationale**: A genuinely broken phonemizer or an uncached voice/model would otherwise corrupt audio or
fail late, unnoticed. Probing also resolves the "first job pays cold-start" issue (prewarm). This is a
correctness guard, not deployment tooling. (The earlier "espeak-ng on PATH" framing came from older
misaki that used the system binary; this install bundles it.)

**Alternatives rejected**: lazy detection (today's behavior — corrupts silently / fails late); a static
HF-cache path check only (doesn't prewarm and is brittle to cache layout — a tiny real synth is the
ground truth).

## D12 — Performance: invariant-safe wins only; cross-job cache rejected (perf-notes.md)

**Decision**: apply `torch.inference_mode()` around synthesis (audit #6), memoized ffmpeg path (D5),
full-digest filenames (D4), and **keep per-job sha256 dedupe**. **Reject** the cross-job LRU cache.

**Rationale**: Measured: synthesis = 93% of compute and is model-bound on one core (batching = 0% gain;
the model already uses `@torch.no_grad()` so inference_mode is small but free; the audio-generation
computation is unchanged (the engine is non-deterministic per call regardless)). The
cross-job cache is the only thing that could help cross-deck repeats but is forbidden by the constitution
(no additional caches in v1; flat disk). So the safe wins are redundant-work removal + the existing
dedupe. See [perf-notes.md](./perf-notes.md) for before/after.

**Alternatives rejected**: cross-job cache (Constitution); batching (0% measured); overlapping encode
with synthesis (timeshares one core; encode is only 7%); the "double float32 conversion" micro-opt
(audit confirmed it's a no-op — `np.asarray` on float32 doesn't copy).

## D13 — Empty-Front placeholder kept; guid scheme kept; both reconciled in docs (audit E2, E3)

**Decision**: KEEP the placeholder `(no prompt — reveal the answer)` (genanki verified: empty Front →
0 cards) and KEEP the guid scheme `guid_for(deck_name, index, front, back, audio_filename)` (the brief's
recommended "deck stem + row index + content"). Record both as deliberate, documented decisions and
reconcile spec.md / research(001) accordingly.

**Rationale**: Both code behaviors are correct per the brief; the defect is purely doc-drift. Verified
genanki behavior and guid stability/distinctness empirically.

## Reconciliation map (doc-drift → artifact edits)

All drift items G1–G15 in audit-notes.md are reconciled by editing the 001 artifacts + CLAUDE.md so they
match the corrected code: resume/DELIVERED wording (tasks 001), guid description (research 001), fidelity
rule FR-012/SC-003 + edge cases + clean_for_speech/parse_deck wording (spec 001, data-model 001,
contracts 001), empty-Front deviation (spec 001), module/FR counts (plan 001, checklist 001), lifecycle
states without PACKAGING (data-model 001, contracts 001), build_application/main signatures + offline
env + delivery ready-message ordering + media-count wording + field-count test (contracts 001, tasks 001).
