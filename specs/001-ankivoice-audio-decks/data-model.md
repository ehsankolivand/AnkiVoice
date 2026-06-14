# Data Model: AnkiVoice — Audio-Enhanced Anki Decks

This feature has exactly one persistent datastore: the SQLite **job store** (Constitution: Resource &
Operational Constraints — "no additional databases"). Everything else is in-memory values or
on-disk working files scoped to a job.

## Entities

### Job (persisted in SQLite)

The durable unit of work. One Job per accepted Submission per user.

| Field | Type | Notes |
|-------|------|-------|
| `id` | INTEGER PK AUTOINCREMENT | Monotonic; also defines **arrival order** (FCFS). |
| `user_id` | INTEGER NOT NULL | Telegram user id (the requester). |
| `chat_id` | INTEGER NOT NULL | Where to deliver the result + messages. |
| `input_path` | TEXT NOT NULL | Absolute path to the saved uploaded file inside the job working dir. |
| `original_filename` | TEXT | The user's filename (for a friendly output name). |
| `state` | TEXT NOT NULL | One of the JobState values below. |
| `error_reason` | TEXT NULL | Friendly reason set when `state = failed`. |
| `created_at` | TEXT NOT NULL | ISO-8601 UTC; tie-breaker/observability. |
| `updated_at` | TEXT NOT NULL | ISO-8601 UTC; updated on every state change. |

Indexes: `(state)` for queue scans; `(user_id)` for the one-active-job check.

**JobState** (lifecycle; persisted as text):

```
queued        -> claimed by the worker (FCFS)
synthesizing  -> generating audio for every usable card (the serialized, one-at-a-time step)
packaging     -> building the .apkg with bundled media
uploading      -> delivering: archive copy first, then user copy
delivered     -> both uploads succeeded; ready to clean
cleaned       -> working dir + outputs removed (TERMINAL, success)
failed        -> validation/processing error; friendly reason recorded; files cleaned (TERMINAL)
```

**State transitions**

```
queued → synthesizing → packaging → uploading → delivered → cleaned   (happy path)
queued/synthesizing/packaging/uploading → failed                       (error path; then scoped cleanup)
```

**Invariants** (enforced by the store + worker; covered by tests):

- **At most one active Job per user**: a user may not have more than one Job in a non-terminal state
  (`queued, synthesizing, packaging, uploading, delivered`). Enqueue is rejected otherwise (FR-020).
- **At most one Job synthesizing at any instant**: only the single worker advances a Job into
  `synthesizing`, and it awaits completion before claiming the next (FR-017, Principle I).
- **FCFS**: the worker always claims the `queued` Job with the smallest `id` (FR-017).
- **Restart-resume**: on startup, Jobs left in a *rebuildable* in-progress state
  (`synthesizing, packaging, uploading`) are reset to `queued` so they are rebuilt and re-delivered
  from their still-present input file (FR-021, SC-010). `delivered`-but-not-`cleaned` Jobs are NOT
  requeued (both copies already went out — re-delivering would double-send); instead the worker
  removes their working dir and marks them `cleaned` at startup. Rationale: intermediate artifacts
  after a crash cannot be trusted; rebuilding from the persisted input is the simplest correct
  recovery. (Tradeoff: a crash *mid-delivery* may, on resume, re-send the package — a rare duplicate
  to the archive and/or user; accepted as rare and harmless. See research.md.)

**Queue position** (FR-018): for a given queued Job, position = count of Jobs with state in
`{queued, synthesizing, packaging}` whose `id` ≤ this Job's `id` (i.e. how many are ahead of or at the
head, including the one currently synthesizing). Reported to the user on acceptance.

### Submission (transient)

The uploaded file as received, before it becomes a Job. Validated by size at the handler
(reject > max file bytes → friendly error, no Job created) and by content at parse time. Once
accepted, it is saved to the job working dir and represented by a Job.

### ParsedDeck → Card (in-memory, derived during synthesizing)

Produced by the parser from the saved input file; never persisted.

**ParsedDeck**

| Field | Type | Notes |
|-------|------|-------|
| `cards` | list[Card] | Usable cards, in input order. |
| `skipped_empty_back` | int | Count of rows skipped because Back was empty (FR-008). |

**Card**

| Field | Type | Notes |
|-------|------|-------|
| `front` | str | Original Front field, preserved byte-for-byte for display (FR-012). MAY be empty. |
| `back` | str | Original Back field, preserved byte-for-byte for display (FR-012). |
| `spoken` | str | Cleaned text for synthesis only: HTML-entity-decoded + CSV-quote-unwrapped (FR-011). Never displayed. |

Usable card = non-empty Back; Front may be empty. Rows with an empty Back or no TAB are skipped and
counted (`skipped_empty_back`). Parser failure modes (raise a typed validation error with a friendly
reason; FR-004..FR-007): `WRONG_FORMAT` (undecodable bytes, or no data row contains a TAB), `EMPTY`
(TABs present but zero usable cards), `TOO_MANY_CARDS` (> max cards). Oversize file is rejected earlier
at the handler by byte size (`TOO_LARGE`).

### Deck Package (on-disk output, scoped to job dir)

The `.apkg` built by the packager: one Anki note per Card showing the original Back text plus an
audio field rendered as `[sound:<file>.mp3]` (auto-play on answer reveal + replay button; FR-013..016),
with all per-card MP3s attached as bundled media. Identical `spoken` strings within a deck reuse one
MP3 (cache keyed on `sha256(spoken)`), so duplicate sentences synthesize once (Constitution P1).

### Working Directory (on-disk, scoped to job)

`<WORK_DIR>/job_<id>/` holds the saved input file, the per-card MP3s, and the built `.apkg`. The
**only** location cleanup ever deletes (FR-024, FR-025, Principle V). Cleanup verifies the target path
is inside `<WORK_DIR>` before removal.

### Archive Destination (external, operator-owned)

A fixed operator-owned chat/channel id from config; receives the backup copy of every delivered
package (FR-022, SC-008). Not stored in the DB; it is configuration.

## Configuration Keys (environment only — Principle VIII)

| Key | Meaning |
|-----|---------|
| `ANKIVOICE_BOT_TOKEN` | Bot auth token (secret). |
| `ANKIVOICE_ARCHIVE_CHAT_ID` | Operator-owned archive destination id. |
| `ANKIVOICE_DEFAULT_VOICE` | Default American-English voice id. |
| `ANKIVOICE_MAX_CARDS` | Per-job maximum card count. |
| `ANKIVOICE_MAX_FILE_BYTES` | Maximum accepted upload size in bytes. |
| `ANKIVOICE_WORK_DIR` | Root working directory for job dirs. |
| `ANKIVOICE_DB_PATH` | SQLite job-store path. |
| `ANKIVOICE_MODEL_DIR` *(opt)* | Local cache dir for the speech model/voices (offline). |

Exact default values and any additional tuning keys are pinned in `plan.md` / `research.md` and shipped
in `.env.example`.
