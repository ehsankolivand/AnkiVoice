# Feature Specification: AnkiVoice — Quality, Bug-Fix & Performance Increment

**Feature Branch**: `002-quality-bugfix-perf`

**Created**: 2026-06-15

**Status**: Draft

**Input**: User description: "AnkiVoice Quality, Bug-Fix & Performance increment (cycle 002) on the
existing audio-deck bot — flawless, internally-consistent, faster behavior without changing the
product's purpose. Reconcile spec↔code↔contract drift, fix every confirmed bug, add a fail-fast
startup guard, keep disk strictly flat, never duplicate a delivery, and speed up the hot path where
safe. Deployment/easy-install is out of scope."

This increment hardens the existing product (spec
[`001-ankivoice-audio-decks`](../001-ankivoice-audio-decks/spec.md)). It changes behavior only where a
confirmed bug, a real technical constraint, or an internal contradiction forces a deliberate decision;
every such decision is recorded here and reflected in all 001 artifacts so code and docs tell one
story. Confirmed defects and divergences are catalogued in
[`audit-notes.md`](./audit-notes.md); measured profiling is in [`perf-notes.md`](./perf-notes.md).

## Clarifications

### Session 2026-06-15

Self-resolved from the repo, the constitution, audit-notes.md, perf-notes.md, and research.md (no open
questions; recorded here because each affects test/implementation design):

- Q: Is the bounded delivery retry in-process, and how many attempts? → A: In-process — a small bounded
  number of attempts (default 3) with exponential backoff inside the same delivery task; on final
  failure the job is retained (not deleted) for restart-resume. No unbounded loop. (IR-015)
- Q: How is the datastore bound enforced, and what is the default? → A: Keep the most-recent N terminal
  (cleaned/failed) job rows (default 500), pruning older terminal rows at startup; active jobs are never
  pruned. (IR-013)
- Q: What bounds the audio-encode step? → A: A per-encode timeout (default 120 s); on timeout the encode
  raises a clear error rather than hanging the single worker. (IR-018)
- Q: With line-by-line parsing, are answer fields with a raw embedded newline inside balanced quotes
  preserved as one field? → A: No — fields are split at line boundaries so rows can never merge (the
  one-card-per-row guarantee, IR-001, takes priority). A raw multi-line quoted field is treated as
  separate lines; genuine Anki exports use HTML `<br>` for in-field line breaks, not raw newlines, so
  this is a deliberate, accepted trade-off.
- Q: How does the startup guard verify the configured voice/model offline? → A: By performing a one-word
  real synthesis with the configured voice — the ground-truth availability check that also prewarms the
  model (IR-010, IR-011); skippable via `ANKIVOICE_SKIP_PREFLIGHT` for tests/dev.
- Q: Must a performance change keep the audio "byte-identical"? → A: No — measured: the speech engine
  is inherently non-deterministic per call (two synthesis runs of the same text differ regardless of
  any optimization). The correct criterion is that a performance change keeps the **audio-generation
  computation unchanged** (same model, voice, parameters, and code path); exact byte-equality across
  runs is not asserted. (Display TEXT remains exactly preserved — that is deterministic.)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - No silent loss or corruption; the displayed text is faithful (Priority: P1)

A learner sends a real-world export that is slightly unusual — an answer that begins with a quotation
mark, a file saved with a byte-order mark, an answer that is only a non-breaking space, or two
identical rows. Today some of these silently drop cards, merge several rows into one, or alter the
shown text. After this increment, every usable row becomes exactly one card whose **displayed** answer
is the same text a normal import would show and whose **audio** is clear and complete.

**Why this priority**: Silently losing or corrupting a learner's cards is the worst failure a
conversion bot can have — it is invisible until the learner studies wrong material. This is the core
trust guarantee and the highest-value fix in the increment.

**Independent Test**: Feed the parser/pipeline the edge-case decks (leading-quote answer, BOM-prefixed
file with Anki headers, answer that is only an encoded space, duplicate rows) and confirm: correct
usable-card count (one per usable row, none merged or dropped), displayed text equals the
normal-import value, and one correct audio per distinct answer.

**Acceptance Scenarios**:

1. **Given** an answer field that begins with a quotation mark (e.g. `"Break a leg" means good luck.`),
   **When** the deck is parsed, **Then** it still produces one card whose displayed answer keeps the
   user's quotation marks exactly, and no following rows are absorbed into it.
2. **Given** a file that contains several rows and one row whose answer begins with an unmatched
   quotation mark, **When** the deck is parsed, **Then** every row still becomes its own card (no rows
   are silently merged or lost).
3. **Given** a file exported with a byte-order mark followed by Anki header lines, **When** the deck is
   parsed, **Then** the header lines are recognised and skipped and no header becomes a card.
4. **Given** a genuine transport-quoted field (surrounding quotes with doubled inner quotes, as a
   normal export produces), **When** the card is produced, **Then** the surrounding quotes are removed
   and the doubled inner quotes collapse to single quotes — matching what a normal import shows.
5. **Given** an answer whose only content is an encoded space or markup that cleans to nothing, **When**
   the deck is parsed, **Then** that row is skipped and counted as having no usable answer rather than
   producing a silent/blank-audio card.
6. **Given** two identical rows, **When** the package is imported, **Then** they remain two distinct
   studyable cards; **and given** the same unchanged file is re-imported, the cards update in place
   rather than duplicating.
7. **Given** an answer with an empty prompt (empty Front), **When** the package is imported, **Then** a
   studyable card is still generated (a neutral placeholder stands in for the empty question side,
   because a card whose question side renders empty is not generated), while the answer text is shown
   unchanged.

---

### User Story 2 - Refuse to start rather than produce silently-wrong audio (Priority: P1)

An operator starts the service on a host that is missing the speech phonemizer, missing the audio
encoder, or that has not cached the configured voice/model for offline use. Today the service starts
anyway and later produces audio with words silently dropped, or fails the first job with a generic
error. After this increment the service performs a fast preflight and, if it cannot produce correct
audio, refuses to start with a specific message naming exactly what is missing and how to fix it.

**Why this priority**: A missing phonemizer silently corrupts every deck's audio with no error — a
correctness failure, not a deployment nicety. Failing fast at startup converts an invisible corruption
into an obvious, actionable message before any user is affected.

**Independent Test**: With the phonemizer absent, the encoder absent, or the configured voice not
cached, starting the service exits immediately with a clear message naming the specific missing
dependency; with all present, startup proceeds (and the model is warm before the first job).

**Acceptance Scenarios**:

1. **Given** the speech phonemizer cannot phonemize (e.g. its bundled library is missing/broken), **When**
   the service starts, **Then** it stops immediately with a specific message identifying the speech-engine
   problem and how to fix it (re-run the warm-up / reinstall deps), and never accepts a job.
2. **Given** the audio encoder is not available, **When** the service starts, **Then** it stops
   immediately with a specific message naming the encoder.
3. **Given** the configured voice or speech model is not cached for offline use, **When** the service
   starts, **Then** it stops with a specific message naming the missing voice/model and pointing to the
   one-time warm-up step.
4. **Given** all required dependencies are present, **When** the service starts, **Then** it starts
   normally and the first job does not pay a cold-start model-load penalty.

---

### User Story 3 - Disk stays strictly flat and the datastore stays bounded (Priority: P2)

An operator runs the service for a long time on a tiny disk. Today, although each job's working
directory is removed, the packaging engine leaves a temporary file outside that directory on every
build, and finished job records accumulate in the datastore forever. After this increment, processing
any number of decks leaves disk usage at its baseline (no stray engine temp files) and the datastore
does not grow without bound.

**Why this priority**: Unbounded disk or datastore growth is the most likely way the service dies on a
small host. The flat-disk guarantee must hold including files created by dependencies, not just files
the service creates directly.

**Independent Test**: Process many decks; confirm no temporary files remain anywhere outside a job's
own working area (including the system temp area) and that the count of retained finished job records
is bounded by a configured limit.

**Acceptance Scenarios**:

1. **Given** a deck is processed and its working directory is cleaned, **When** the operator inspects
   the system temporary area, **Then** no leftover packaging temp files remain.
2. **Given** many decks have been processed and delivered over time, **When** the operator inspects the
   datastore, **Then** the number of retained terminal (finished/failed) job records is bounded by a
   configured maximum, not growing one-per-job forever.
3. **Given** cleanup runs, **When** it removes files, **Then** it still only ever removes files inside a
   job's own working area (never anything outside it).

---

### User Story 4 - Exactly-once delivery, even across a restart, with bounded retry (Priority: P2)

A deck finishes and is being delivered (archive copy first, then the learner) when the service
restarts mid-delivery, or a delivery upload transiently fails. Today a mid-delivery restart can re-send
the package to both destinations (a visible duplicate to the learner), and a failed delivery holds the
learner's slot until the next restart. After this increment, each copy is sent at most once across
restarts, and a transient delivery failure is retried a bounded number of times before being deferred
to restart.

**Why this priority**: A duplicate delivery erodes trust and an indefinitely-held slot blocks the
learner. Exactly-once delivery and bounded retry make restarts and transient failures safe.

**Independent Test**: Simulate a crash after the archive copy is sent but before completion, then
resume: confirm only the missing (learner) copy is sent and the archive is not re-sent; confirm a
fully-delivered job is never re-sent; confirm a transient failure is retried a small bounded number of
times before the job is left for restart.

**Acceptance Scenarios**:

1. **Given** the archive copy has been sent but the service restarted before completion, **When** the
   job resumes, **Then** only the learner copy is sent and the archive copy is not sent again.
2. **Given** a job whose both copies were already sent, **When** the service restarts, **Then** the
   package is not sent to anyone again.
3. **Given** a transient delivery failure, **When** delivery runs, **Then** it retries a small bounded
   number of times with backoff before giving up and deferring to restart (no unbounded retry loop).

---

### User Story 5 - Faster on the representative deck where it is safe (Priority: P3)

A learner submits a typical deck. After this increment the service produces the same package, with the
audio-generation computation unchanged (the engine is non-deterministic per call, so exact byte-equality
is not asserted), at least as fast as before and measurably faster on the redundant-work paths, without
ever weakening the single-core, offline, flat-disk, or content-fidelity guarantees.

**Why this priority**: Throughput on a single core is bounded by the speech engine and cannot be traded
against correctness or the resource budget; the only acceptable speedups are removing redundant work.
This is valuable but strictly subordinate to correctness and the resource bound.

**Independent Test**: On the recorded representative deck, compare before/after wall-clock for package
production; confirm the audio-generation path is unchanged (engine non-deterministic per call, so
byte-equality is not asserted) and no invariant (single core, offline, flat disk, fidelity) is weakened.

**Acceptance Scenarios**:

1. **Given** the representative deck, **When** it is converted before and after the increment, **Then**
   the after time is not slower (within measurement noise) and the audio-generation path is unchanged
   (same model/voice/parameters/code path; exact byte-equality is not asserted because the engine is
   non-deterministic per call).
2. **Given** a deck containing repeated answer sentences, **When** it is converted, **Then** each
   distinct answer is voiced only once (the existing per-job de-duplication is preserved).
3. **Given** the resource budget, **When** any speedup is applied, **Then** it does not introduce
   additional concurrency, an additional cache/datastore, or unbounded disk/memory growth.

---

### User Story 6 - One consistent story across code and documents (Priority: P3)

A maintainer reads any project document — the product spec, the plan, the data model, the research
notes, the contracts, the task list, the agent context — and finds it agrees with the code and with
every other document. Today several documents contradict the code or each other (resume behaviour, the
text-fidelity rule, the empty-prompt placeholder, the de-duplication identity, the lifecycle states,
module counts, requirement counts, interface signatures).

**Why this priority**: An agent-native, agent-extended codebase is only safe to change if its documents
are trustworthy. Inconsistent docs cause wrong changes. This is the reconciliation deliverable.

**Independent Test**: A cross-artifact consistency review finds zero contradictions between any document
and the code or between documents (the same review that `/speckit-analyze` performs).

**Acceptance Scenarios**:

1. **Given** any reconciled behaviour decided in this increment, **When** a maintainer reads the product
   spec, plan, data model, research, contracts, tasks, and agent context, **Then** all of them describe
   that behaviour identically and match the code.
2. **Given** the lifecycle/state descriptions, **When** compared to the code, **Then** the documented
   states are exactly the states the code uses (no documented-but-unused or used-but-undocumented
   state).

### Edge Cases

- **Answer begins with a quotation mark (unbalanced)**: each input row still becomes its own card; no
  rows are merged; the literal quotation marks are preserved in the display.
- **Answer is a genuine transport-quoted field**: surrounding quotes removed and doubled inner quotes
  collapsed (matches a normal import).
- **File saved with a byte-order mark**: the mark is stripped so header detection and the first field
  are correct.
- **Answer cleans to empty** (only an encoded space / markup): skipped and counted as no usable answer.
- **Empty prompt (empty Front)**: a studyable card is still generated via a neutral placeholder.
- **Mid-delivery restart**: only the not-yet-sent copy is delivered on resume; a fully-delivered job is
  never re-sent.
- **Transient delivery failure**: retried a small bounded number of times, then deferred to restart.
- **Missing phonemizer / encoder / uncached voice at startup**: the service refuses to start with a
  specific message.
- **Engine temporary files**: created inside the job's working area so cleanup removes them; disk stays
  flat.
- **Long-running operation**: terminal job records are pruned to a bounded maximum.
- **Stuck audio encoder**: the encode step times out with a clear error rather than hanging the worker.

## Requirements *(mandatory)*

Requirements below are increment requirements (IR) that fix, harden, or clarify the product spec 001.
Each is independently testable and is pinned by a regression test written first (Constitution VII).

### Functional Requirements — lossless & faithful conversion (US1)

- **IR-001**: The system MUST produce exactly one card per usable input row and MUST NOT merge or drop
  rows because an answer field begins with, or contains, a quotation mark.
- **IR-002**: For display, the system MUST show the same field value a normal import would show: it MUST
  remove transport quoting only when a field is a complete, balanced transport-quoted field (surrounding
  quotes with doubled inner quotes) and MUST otherwise preserve the field's characters exactly. It MUST
  NOT reword or alter field text beyond this transport decoding.
- **IR-003**: The system MUST strip a leading byte-order mark before parsing so header lines and the
  first field are interpreted correctly.
- **IR-004**: The system MUST treat a row whose cleaned spoken text is empty or whitespace-only as
  having no usable answer (skipped and counted), the same as an empty answer.
- **IR-005**: For audio, the system MUST voice the same transport-decoded value used for display, with
  HTML entities additionally decoded; identical answer text MUST map to identical audio.
- **IR-006**: An empty prompt (empty Front) MUST still yield a studyable card; the system MAY substitute
  a fixed neutral placeholder for the empty question side (recorded text below), because a card whose
  question side renders empty is not generated by Anki.
- **IR-007**: Two identical input rows MUST remain two distinct studyable cards; re-importing the same
  unchanged file MUST update the existing cards rather than create duplicates.

### Functional Requirements — fail-fast startup guard (US2)

- **IR-008**: The system MUST verify, at startup before accepting any job, that the speech phonemizer is
  available; if not, it MUST refuse to start with a specific, actionable message naming it.
- **IR-009**: The system MUST verify, at startup, that the audio encoder is available; if not, it MUST
  refuse to start with a specific message naming it.
- **IR-010**: The system MUST verify, at startup, that the configured voice and speech model are
  available for offline use; if not, it MUST refuse to start with a specific message naming the missing
  voice/model and pointing to the one-time warm-up.
- **IR-011**: When all dependencies are present, startup MUST make the speech model ready so the first
  job does not pay a cold-start load penalty.

### Functional Requirements — flat disk & bounded datastore (US3)

- **IR-012**: Temporary files created while building a package (including those created by the packaging
  engine) MUST be created inside the job's working area so scoped cleanup removes them; after processing,
  no package temp files MUST remain anywhere outside a job's working area.
- **IR-013**: The system MUST bound the number of retained terminal (cleaned/failed) job records to a
  configured maximum so the datastore does not grow one-record-per-job forever.

### Functional Requirements — exactly-once delivery & bounded retry (US4)

- **IR-014**: The system MUST record, per job, which delivery copies (archive, user) have been sent, and
  on delivery or resume MUST send only copies not yet sent, so no copy is ever sent twice — including
  across a mid-delivery restart.
- **IR-015**: A transient delivery failure MUST be retried a small bounded number of times with backoff
  before the job is left (retained, not deleted) for resume at the next restart; there MUST be no
  unbounded retry loop.

### Functional Requirements — safe performance (US5)

- **IR-016**: Any performance change MUST keep the audio-generation computation unchanged (same model,
  voice, parameters, and code path — the engine is inherently non-deterministic per call, so exact
  byte-equality is not a criterion) and MUST NOT introduce additional concurrency, an additional
  cache/datastore, or unbounded disk/memory growth.
- **IR-017**: The existing per-job de-duplication (each distinct spoken answer voiced once) MUST be
  preserved.
- **IR-018**: The audio-encode step MUST have a bounded execution time (a timeout) so a stuck encoder
  cannot hang the single worker.

### Functional Requirements — one consistent story (US6)

- **IR-019**: Every reconciled behaviour MUST be reflected identically in the product spec, plan, data
  model, research, contracts, tasks, and agent-context documents, and MUST match the code.
- **IR-020**: The documented job lifecycle states MUST be exactly the states the code uses; any state
  that is not meaningfully observed MUST be removed from both code and documents consistently.

### Key Entities *(include if feature involves data)*

- **Job (extended)**: gains durable per-copy delivery flags (archive-sent, user-sent) so delivery is
  idempotent across restarts. Terminal job records are subject to a bounded retention limit.
- **Card / ParsedDeck (clarified)**: a card's displayed Front/Back is the transport-decoded field; the
  spoken text is that value with HTML entities decoded; a row is "usable" only if its cleaned spoken
  text is non-empty.
- **Startup preflight (new)**: a check performed before accepting jobs that the phonemizer, the audio
  encoder, and the configured voice/model are present, failing fast otherwise.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of usable input rows become exactly one card; zero rows are merged or dropped across
  the edge-case decks (leading/unbalanced quotes, BOM, encoded-space-only, duplicates).
- **SC-002**: 100% of cards display the value a normal import would show (transport quotes unwrapped
  only for balanced fields; line endings normalized to a single newline; byte-order mark stripped);
  audio uses that value with entities decoded.
- **SC-003**: With any required dependency (phonemizer, encoder, configured voice/model) missing, the
  service exits at startup 100% of the time with a specific message naming the missing item, and never
  accepts a job in that state.
- **SC-004**: After processing N decks, the count of temporary files outside job working areas is zero,
  and retained terminal job records are ≤ the configured maximum (independent of N).
- **SC-005**: Across a mid-delivery restart, each delivery copy is sent at most once (0 duplicate
  user-visible deliveries); a fully-delivered job is re-sent 0 times.
- **SC-006**: On the representative deck, after-increment package-production wall-clock is ≤
  before-increment (within measurement noise), and the audio-generation path is unchanged (the engine
  is non-deterministic per call, so byte-equality is not measured).
- **SC-007**: A cross-artifact consistency review reports zero contradictions between any document and
  the code or between documents.
- **SC-008**: The full default test suite is green and fully offline; every fixed bug and reconciled
  behaviour is pinned by a test; no test is weakened or skipped to pass.

## Assumptions

- **Reused product spec & constitution**: This increment builds on 001 and does not change the
  constitution; all eight principles remain binding. Where 001 documents diverged from the code, the
  code's corrected behaviour (per audit-notes) is authoritative and the documents are updated to match.
- **Content-fidelity meaning**: "Preserve the user's text" means preserve the field value a normal
  import would show. Transport quoting (balanced surrounding quotes / doubled inner quotes), line-ending
  variants, and a leading byte-order mark are transport encoding, not content; normalizing them is
  faithful, not a rewrite. This refines 001 FR-012 / SC-003 and is recorded there.
- **Empty-prompt placeholder**: A fixed neutral placeholder — `(no prompt — reveal the answer)` — stands
  in for an empty question side. This is a deliberate, recorded deviation from "the front shows
  nothing," forced by Anki not generating a card with an empty question side (verified).
- **De-duplication identity**: A card's re-import identity is derived from the deck name (filename stem)
  plus the row position plus the row content. Re-importing the same unchanged file updates in place;
  renaming the file or editing a card's text yields new cards on re-import (accepted, recorded).
- **No cross-job audio cache**: A persistent cross-job audio cache is rejected because the constitution
  forbids additional caches/datastores in v1 and requires flat disk; only the per-job de-duplication is
  used. (Recorded in perf-notes and the plan's Constitution Check.)
- **Bounded retry**: "A small bounded number" of delivery retries means a few attempts with backoff
  (operator-tunable), never an unbounded loop; the existing restart-resume remains the durable backstop.
- **Defaults are operator-overridable**: New limits (terminal-record retention, encode timeout, delivery
  retry count/backoff) ship with safe defaults via environment configuration (Constitution VIII).

## Out of Scope (this increment)

- Deployment, packaging-for-install, service supervision, and any easy-install tooling (a later spec).
- Any change to the product's purpose, the voiced side, the one-voice model, or the chat-only channel.
- A persistent cross-job audio cache, GPU/parallel synthesis, or any additional datastore/cache/service.
- New user-facing features beyond the bug fixes, the startup guard, and the reconciliations above.
