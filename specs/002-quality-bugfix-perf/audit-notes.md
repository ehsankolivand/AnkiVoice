# AnkiVoice — Adversarial Audit Notes (cycle 002)

Method: full manual read of every source/test/spec artifact, empirical probes against the real code,
and a parallel adversarial audit (8 dimensions × finder + independent verifier subagents; 58 agents,
50 candidate findings → **46 confirmed, 4 correctly rejected**). Every finding below was confirmed
against the ACTUAL code with a reproduction or exact file:line; speculation excluded. Baseline suite:
82 passed, 1 deselected.

Legend: severity is the **verifier-corrected** severity. ✅=fix this cycle, with a failing regression
test first (TDD). Doc-drift items are reconciled across spec/plan/data-model/research/contracts/tasks/
CLAUDE.md so code and docs tell one story.

---

## A. Correctness bugs (regression-test-first)

### A1 ✅ HIGH — CSV reader swallows rows / strips literal quotes (NEW)
`parser.parse_deck` feeds the whole body to `csv.reader(StringIO(body), delimiter='\t', quotechar='"')`
(parser.py:71). A Back field that *begins* with a `"` is treated as an opening CSV quote:
- **Unbalanced** leading quote → the reader consumes across newlines until the next quote, **merging
  multiple input rows into one card** → silent card loss (violates FR-008 "one card per usable row").
  Repro: `parse_deck(b'q1\t"This quote is unbalanced.\nq2\tThis row gets swallowed.\nq3\t...\n')` → **1**
  card instead of 3. The `except csv.Error` (parser.py:83) does NOT fire (EOF just ends the field).
- **Balanced-but-not-wrapped** leading quote → quote chars stripped from the displayed Back (FR-012).
  Repro: `'idiom\t"Break a leg" means good luck.'` → back `Break a leg means good luck.` (quotes gone).

DECISION/FIX: split **line by line** (rows can never merge → guarantees FR-008), split each data line on
the **first TAB** (matches FR-003 / the contract), and unwrap a field for display+speech **only when it
is a complete balanced wrapped field** (`"…"` with internal `""`→`"`). Genuine Anki exports (which escape
quotes as `""`) round-trip; hand-edited literal quotes are preserved byte-for-byte. Resolves A1 and the
FR-003-vs-FR-011 tension, and makes `clean_for_speech` actually do the unwrap its contract claims.

### A2 ✅ HIGH — UTF-8 BOM defeats header skipping (NEW; = my probe finding N)
`raw.decode("utf-8")` (parser.py:43) keeps a leading BOM (`﻿`). `﻿#separator` does not
`startswith("#")` (BOM is not whitespace, `lstrip()` won't drop it), so the Anki header block is not
skipped; `#columns:Front\tBack` (which has a TAB) becomes a **junk card** `front='#columns:Front',
back='Back'`. Repro confirmed. FIX: `raw.decode("utf-8-sig")` (strips a leading BOM, still raises on
truly-undecodable bytes → WRONG_FORMAT). Common on Windows Anki exports.

### A3 ✅ LOW — MP3 filename uses 16-hex truncated sha256 → prefix collision overwrites audio
`pipeline.py:43` names files `sha256(spoken)[:16]` while the dedupe cache keys on the **full** spoken
string. Two distinct sentences sharing a 16-hex prefix → same filename → one MP3 overwrites the other →
a card plays the wrong audio. Probability is tiny (≤200 cards) but it is a latent correctness bug. FIX:
use the **full** hexdigest for the filename. Pin with a test asserting the media filename derives from
the full digest.

### A4 ✅ LOW — A Back that is only an HTML-encoded space voices a blank
`back='&#32;'` passes `back.strip() != ""` (raw) so it is "usable", but `spoken` cleans to `" "` →
empty/blank audio. FIX: also skip+count a card whose **cleaned spoken** text is empty/whitespace-only
(FR-008 "cannot be voiced"). Document in edge cases.

### A5 ✅ LOW — `encode_mp3` ffmpeg subprocess has no timeout
`audio.py:47 subprocess.run(...)` can hang forever on a stuck encoder, freezing the (single) worker.
FIX: pass a generous `timeout=` and raise a clear `RuntimeError` on `TimeoutExpired`.

## B. Cleanup / disk safety

### B1 ✅ MEDIUM — genanki leaks a SQLite temp file into the system TMPDIR (NEW)
`genanki Package.write_to_file` calls `tempfile.mkstemp()` (package.py:30) and **never removes** the temp
DB. Each build leaks a ~52 KB file into the system temp dir — **outside WORK_DIR**, so scoped cleanup
misses it and disk grows with N jobs (breaks P5 / FR-024 / SC-006 flat-disk). Repro: 3 builds → 3 orphan
`tmp*` (53248 B) after all job dirs cleaned. FIX: redirect `tempfile.tempdir` to the output's job dir for
the duration of `write_to_file` so the temp lands inside the job dir and is scoped-cleaned. (Safe: builds
are serialized — one synthesis/packaging at a time.) Pin with a leak regression test.

## C. Startup correctness guard (the <correctness_guards> requirement; findings C/I/#5/#14/#17)

### C1 ✅ HIGH — No fail-fast guard for missing espeak-ng (silent audio corruption)
Without espeak-ng, misaki drops out-of-dictionary words **silently** from the audio (research.md:66).
Today `python -m ankivoice` starts fine and produces corrupt audio with no error. Also: ffmpeg absence
is only caught lazily at first encode (#14), and an uncached configured model/voice fails the first job
offline with a generic error (#5), and the model loads lazily inside the first job (#17, no prewarm).
FIX: a new `preflight.py` run from `__main__` before `run_polling()` that fails fast with specific
messages if: espeak-ng not on PATH; ffmpeg not on PATH; or the configured Kokoro weights + configured
voice are not available offline. Probing the model also prewarms it (resolves #17). This is a correctness
guard, not deployment tooling.

## D. Concurrency / queue / durability

### D1 ✅ (robust) — Per-copy delivery idempotency (finding A in brief; audit-rejected-as-documented)
Today a crash *mid-delivery* (after archive send, before `DELIVERED`) requeues the job (UPLOADING is
rebuildable) → on resume the package is rebuilt and **re-sent to BOTH** archive and user (user-visible
duplicate). The DELIVERED-resume bug itself is already correct (store requeues only rebuildable; worker
cleans DELIVERED). FIX (robust): add `archive_sent`/`user_sent` flags to the jobs row; `deliver()` skips
an already-sent copy and sets each flag right after its send; resume then re-sends only the missing copy
and a fully-delivered job is never re-sent. Make tasks.md/store/data-model/test agree.

### D2 ✅ LOW — One-active-per-user is non-atomic check-then-insert (finding B)
`bot.on_document` does `has_active_job()` then `enqueue()` (safe only because PTB defaults to
`max_concurrent_updates=1`); `store.enqueue` never refuses despite data-model.md claiming "Enqueue is
rejected otherwise". FIX: add atomic `store.enqueue_if_no_active(...)` (single transaction conditional
insert) returning `None` on refusal; bot uses it and, on refusal after a download, deletes the orphan
and messages the user. Belt-and-suspenders even under future multi-threading.

### D3 ✅ DOC+CODE — Remove the dead PACKAGING state (finding K; audit #24/#36/#45)
`worker._process` sets `PACKAGING` *after* `build_package` already finished synth+packaging (worker.py:113),
then `deliver()` immediately sets `UPLOADING` — so PACKAGING never marks packaging work; its only real job
is "moved out of SYNTHESIZING before the next claim". FIX: worker sets `UPLOADING` synchronously right
after the build (preserving the one-SYNTHESIZING invariant); remove `PACKAGING` from the enum, `_ACTIVE`,
`_AHEAD`, `_REBUILDABLE`; resume maps any legacy `'packaging'` row to rebuildable. Update data-model/tasks/
contracts/tests. `_AHEAD` becomes `(QUEUED, SYNTHESIZING)`.

### D4 ✅ LOW — Unbounded jobs-table growth (finding J; audit #10)
Terminal rows (CLEANED/FAILED) are never deleted → SQLite grows with use even though WORK_DIR stays flat.
FIX: `store.prune_terminal_jobs(keep=...)` called at startup, keeping the most recent N terminal rows.
Document as bounded.

### D5 ✅ LOW — Failed delivery holds the slot + retains the dir until restart (finding M; audit #12)
FIX: bounded in-process delivery retry (a few attempts with backoff) before deferring to restart; keep it
simple (no unbounded loop). Combined with D1 (idempotent deliver) retries never double-send.

## E. .apkg correctness

### E1 ✅ LOW — `build_apkg` doesn't validate `[sound:]` basenames match bundled media (audit #13)
Docstring states it as a requirement but nothing enforces it; a mismatch silently ships a card whose
audio won't play. FIX: assert every card's `audio_filename` has a matching media-path basename.

### E2 — Empty-Front placeholder is REQUIRED (finding D; VERIFIED)
genanki 0.13.1 confirmed: empty Front + `qfmt={{Front}}` → `note.cards==0` (no studyable card); a
placeholder front → 1 card. KEEP a minimal placeholder. RECONCILE spec (edge case + FR-003) to record
this deliberate, documented deviation from "the front shows nothing". Exact placeholder text fixed in
spec.

### E3 — guid scheme (finding F; audit #21/#31/#38)
Code uses `guid_for(deck_name, index, front, back, audio_filename)` = **deck stem + row index + content**,
which is EXACTLY the brief's recommended scheme: identical rows stay distinct (index) and re-importing the
**same unchanged file** updates rather than duplicates. Accepted trade-offs (documented): renaming the file
or editing a card's text yields new cards on re-import. RECONCILE research.md Decision 3 (says "content
hash") to describe the actual scheme.

## F. Performance (see perf-notes.md for measured before/after)

Hot path = per-sentence Kokoro inference on one core (**93%** of compute; ~700 ms/sentence; encode 7%).
Measured: batching = 0% gain; double numpy conversion = no-op (audit-rejected). **Cross-job LRU cache
REJECTED** — constitution forbids "additional databases, caches, or services in v1" and requires flat
disk; keep per-job sha256 dedupe only. Safe wins applied: **F1** `torch.inference_mode()` wrap
(audit #6; ~5% per sentence, byte-identical PCM); **F2** memoize the ffmpeg path (audit #15, remove a PATH
scan per unique sentence); **F3** full-digest filenames (also fixes A3); keep per-job dedupe.

## G. Doc-drift to reconcile (code is right; fix the artifacts)

| # | Drift | Resolution |
|---|---|---|
| G1 | tasks.md T022/T023: "requeue resets DELIVERED→QUEUED" | code/test reset only rebuildable; reword (#23/#37) |
| G2 | research Decision 3 guid "content-hash" | describe deck stem+index+content (#38, see E3) |
| G3 | FR-012/SC-003 "byte-for-byte" vs unwrap + line-ending norm + BOM strip | amend to "the decoded field Anki imports: transport quotes unwrapped, line endings normalized to LF, BOM stripped; never reworded" (#27/#39) |
| G4 | contract clean_for_speech "strips CSV quotes" vs code only html.unescape | after A1 fix, code DOES balanced-unwrap+unescape → align contract (#20/#28/#40) |
| G5 | spec edge case "empty Front shows nothing" vs placeholder | record deviation (#30/#41, see E2) |
| G6 | plan "12 modules"/"12 interfaces" vs 13 (now 14 w/ preflight) | fix counts + module list (#42) |
| G7 | checklist "30 FRs (FR-001..FR-030)" vs FR-001..FR-031 | fix count (#43) |
| G8 | data-model skipped_empty_back "Back-empty only" omits no-TAB rows | fix wording (#22/#29) |
| G9 | contract build_application(config, store) missing synthesizer; __main__ sig | fix signatures (#34/#44) |
| G10 | data-model/spec "split on first TAB" vs csv-all-tabs | after A1, code splits on first TAB → consistent (#19) |
| G11 | delivery contract ready-message ordering vs code | reorder doc to match (ready msg after DELIVERED, best-effort) (#25) |
| G12 | HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE/ANKIVOICE_ALLOW_DOWNLOADS undocumented | document in research/.env.example/data-model/config (#35) |
| G13 | tasks media-count "one audio per usable card" vs sha256 dedupe | reword "one per distinct spoken" (#32) |
| G14 | tasks T015 "Note field-count guard" test absent | add a tiny field-count test (genanki raises) (#33) |
| G15 | parser csv.Error path effectively dead | removed by A1 line-by-line rewrite (#26) |

## Correctly REJECTED by the audit (no action / decision recorded)
- Cross-job audio cache — optional enhancement; rejected on constitution grounds (see F).
- `synthesize()` "double float32 conversion" — `np.asarray` on float32 doesn't copy; no-op. Skip.
- warm-up "doesn't warm configured voice" — it DOES read ANKIVOICE_DEFAULT_VOICE; not a bug (finding I).
- research "understates mid-delivery duplicate window" — real but addressed by D1 (per-copy flags).
