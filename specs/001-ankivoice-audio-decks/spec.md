# Feature Specification: AnkiVoice — Audio-Enhanced Anki Decks

**Feature Branch**: `001-ankivoice-audio-decks`

**Created**: 2026-06-14

**Status**: Draft

**Input**: User description: "AnkiVoice — a chat bot that turns a user's text-based Anki deck into an audio-enhanced Anki package with clear, natural, native-accent English speech for pronunciation practice."

## Clarifications

### Session 2026-06-15

- Q: Is a row with an empty Front but a valid Back usable? → A: Yes — only the Back (answer) is
  required; an empty Front is allowed (e.g. a cloze/blanked prompt) and still produces a usable card.
- Q: How are data rows that contain no TAB handled, and when is the input WRONG_FORMAT vs EMPTY? → A:
  A data row with no TAB has no Back and is skipped and counted (like an empty-Back row). WRONG_FORMAT
  applies only when no data row contains a TAB at all (the file is not tab-separated) or the bytes are
  not decodable; EMPTY applies when TABs exist but zero usable cards remain after skipping.
- Q: What happens when a delivery upload (archive or user) fails? → A: The job is retained (the
  package is never auto-deleted) and retried when the service next restarts (resume). There is no
  in-process delivery-retry loop in v1. By contrast, a processing failure (parse/synthesis/packaging)
  terminates the job as failed and its own scoped files are cleaned up.
- Q: What text encoding is assumed for the uploaded file? → A: UTF-8 (Anki text exports are UTF-8);
  bytes that cannot be decoded as UTF-8 are rejected as WRONG_FORMAT with a friendly message.
- Q: How are the output deck name and delivered file named? → A: From the user's original filename
  stem (e.g. `vocab.txt` → deck "vocab", file `vocab.apkg`); if no usable name is available, fall back
  to deck "AnkiVoice deck" / file `ankivoice.apkg`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Turn a valid deck into an audio-enhanced importable package (Priority: P1)

A learner studying English pronunciation has an Anki deck where each card's answer is a complete,
correct English sentence. They export it as a tab-separated text file (a Front column and a Back
column) and send that file to the bot. The bot generates clear, natural, native-accent English
speech for the answer side of every card (the complete Back sentence — not the blanked Front) and
sends back an Anki package. When the learner imports the package and reveals a card's answer, the
card shows the original answer text exactly, automatically plays that card's audio, and offers a
replay button to hear it again.

**Why this priority**: This is the entire reason the product exists — converting a silent deck into
one that speaks the answer. On its own it is a complete, demonstrable MVP: one user, one valid file,
one audio-enhanced package back.

**Independent Test**: Send one small valid export; receive a package; import it into Anki; reveal
each answer and confirm the correct audio auto-plays and the replay button works; confirm the
displayed answer text is byte-for-byte identical to the original.

**Acceptance Scenarios**:

1. **Given** a valid tab-separated export with several cards whose Back fields are complete English
   sentences, **When** the user sends it to the bot, **Then** the bot returns an Anki package that
   imports without errors and contains one card per usable input row.
2. **Given** the imported package, **When** the user reveals a card's answer, **Then** the original
   Back text is displayed unchanged AND the audio of that Back sentence plays automatically.
3. **Given** a revealed card, **When** the user activates the replay control, **Then** the same
   audio plays again.
4. **Given** an input field that contains HTML entities or surrounding quotes, **When** the card is
   produced, **Then** the spoken audio uses the clean, human-readable sentence while the displayed
   text still shows the user's original field exactly.
5. **Given** the audio is for the answer, **When** the front (prompt/blanked) side is shown, **Then**
   no answer audio is played on the front.

---

### User Story 2 - Strict one-at-a-time, fair, durable queue (Priority: P2)

Multiple learners (or one learner sending several files) use the bot around the same time. Because
the service runs on a very small machine, only one deck can be turned into speech at a time. Each
request is acknowledged immediately with its position in line, processed strictly in the order it
arrived, and the next request begins only after the current one finishes synthesizing. If the
service restarts, work that was queued or in progress is resumed rather than lost.

**Why this priority**: Correct, fair, durable serialization is what keeps the public bot alive and
trustworthy under load. It is built on top of US1's conversion but is independently valuable and
testable: it guarantees ordering, acknowledgement, and crash-resilience.

**Independent Test**: Submit two files at nearly the same moment; confirm both senders receive a
queue-position acknowledgement, the second deck is synthesized only after the first finishes, and
the first deck's delivery overlaps the second deck's synthesis; restart the service mid-run and
confirm the unfinished deck still completes and is delivered.

**Acceptance Scenarios**:

1. **Given** the bot is idle, **When** a user sends a valid file, **Then** the bot confirms receipt
   and tells the user their position in line.
2. **Given** one deck is being synthesized, **When** a second user sends a file, **Then** the second
   user is told their position and their deck is not synthesized until the first finishes.
3. **Given** two queued decks, **When** the first finishes synthesizing, **Then** the first deck's
   delivery may proceed at the same time the second deck's synthesis begins (delivery overlaps the
   next synthesis), while still only one synthesis runs at a time.
4. **Given** a user already has a deck queued or being processed, **When** they send another file,
   **Then** the bot tells them a deck is already being processed and declines to start a second
   active job for them.
5. **Given** a deck is queued or mid-processing, **When** the service restarts, **Then** that deck
   resumes and is eventually delivered without the user resending it.

---

### User Story 3 - Archive backup, ready-notification, and always-clean-up (Priority: P3)

When a deck is finished, the bot first sends a backup copy to a fixed operator-owned archive
destination, then delivers the package to the requesting user with a clear, friendly "your deck is
ready" message. Only after both the archive copy and the user copy have been sent successfully does
the bot remove the package and the job's working files from the server. Temporary and output files
are always cleaned up — on both success and failure — so the server's disk usage stays flat and does
not grow as more decks are processed. Cleanup only ever removes the job's own files.

**Why this priority**: The archive gives the operator a durable backup of every delivery, and the
always-clean-up guarantee is what lets the service run indefinitely on a tiny disk. It depends on a
deck being produced (US1) but is an independently testable operational guarantee.

**Independent Test**: Process a deck; confirm the package appears in the archive destination before
the user receives it; after delivery confirm no per-job files remain on the server and that disk
usage returned to its baseline; confirm the user received a friendly ready-message.

**Acceptance Scenarios**:

1. **Given** a deck has finished synthesizing and packaging, **When** delivery runs, **Then** the
   package is sent to the operator archive first and to the user second.
2. **Given** both the archive and user copies have been sent successfully, **When** delivery
   completes, **Then** the package and the job's working files are removed from the server.
3. **Given** a job finishes (successfully or with an error), **When** it ends, **Then** that job's
   temporary and output files are removed and disk usage returns to baseline.
4. **Given** many decks have been processed over time, **When** the operator inspects the server,
   **Then** disk usage has not grown with the number of jobs (no per-job files accumulate).
5. **Given** the user copy or archive copy fails to send, **When** delivery cannot complete, **Then**
   the package is retained (not deleted) so the job can be retried/resumed, and no files outside the
   job's own working area are ever removed.

---

### User Story 4 - Clear, friendly, actionable errors for bad input (Priority: P3)

A user sends something the bot cannot turn into a deck — a file in the wrong format, an empty file, a
file that is too large, or a file with more cards than allowed. Instead of failing silently or
crashing, the bot replies with a clear, friendly, specific message that explains what was wrong and
what to do about it. The service stays healthy and leaves no leftover files.

**Why this priority**: Good error handling is essential for a public bot but does not block the core
conversion value. It is independently testable by sending known-bad inputs.

**Independent Test**: Send a non-tab-separated file, an empty file, an oversized file, and a file
exceeding the card cap; confirm each receives a specific, friendly, actionable error message, the
service continues running, and no residual files remain.

**Acceptance Scenarios**:

1. **Given** a file that is not tab-separated / has no usable Front/Back structure, **When** it is
   received, **Then** the user is told the format is wrong and what the expected format is.
2. **Given** an empty file or a file that yields zero usable cards, **When** it is received, **Then**
   the user is told the file is empty / has no usable cards.
3. **Given** a file larger than the allowed size, **When** it is received, **Then** the user is told
   it is too large and what the limit is.
4. **Given** a file with more cards than the allowed per-job cap, **When** it is received, **Then**
   the user is told there are too many cards and what the limit is.
5. **Given** any rejected input, **When** the error is returned, **Then** the service remains running
   and no temporary files from the rejected request remain on disk.

---

### Edge Cases

- **Header lines**: Leading lines beginning with `#` (e.g. `#separator:tab`, `#html:true`) are
  recognized as Anki export headers and skipped, not treated as cards.
- **Quote-wrapped fields**: Fields wrapped in surrounding quotes are unwrapped for speech; the
  displayed text still reflects the user's original field.
- **HTML entities**: Entities such as `&amp;` or `&#39;` are decoded for speech so the audio sounds
  natural; the displayed field is preserved exactly.
- **Empty Back on a row**: A row whose Back value is empty cannot be voiced; it is skipped and
  counted. If skipping leaves zero usable cards, the input is treated as empty/invalid.
- **Empty Front on a row**: A row with an empty Front but a non-empty Back is still a usable card
  (only the Back is required); the front simply shows nothing.
- **Row without a TAB**: A data row containing no TAB has no Back and is skipped and counted. If no
  data row contains a TAB at all, the whole input is rejected as wrong-format (not tab-separated).
- **Non-UTF-8 bytes**: Input is decoded as UTF-8; bytes that cannot be decoded are rejected as
  wrong-format with a friendly message.
- **Duplicate sentences**: Two cards with identical answer sentences each get correct audio for that
  sentence (identical sentences sound identical).
- **Restart mid-job**: A deck that was queued or mid-processing when the service stopped is resumed
  on restart and still delivered.
- **Partial delivery failure**: If the archive copy or the user copy fails, the package is kept for
  retry; it is not deleted until both copies succeed.
- **Burst load**: A sudden burst of files queues and waits; the service slows but does not crash,
  run out of memory, or fill the disk.
- **Repeated submissions by one user**: A second submission while the user already has an active job
  is declined with an explanation rather than creating a second concurrent job.

## Requirements *(mandatory)*

### Functional Requirements

**Input handling & validation**

- **FR-001**: The system MUST accept a tab-separated Anki text-export file sent by a user through the
  chat bot.
- **FR-002**: The system MUST skip leading header lines that begin with `#` (Anki export headers) and
  not treat them as cards.
- **FR-003**: The system MUST read each data row as a Front field and a Back field (split on the
  first TAB), where the Back field is the complete answer sentence. The Front MAY be empty; only the
  Back is required for a usable card. Additional columns beyond Front/Back, if present, are ignored.
- **FR-004**: The system MUST decode the input as UTF-8 and MUST reject, with a clear and specific
  message, an input that cannot be decoded or in which no data row contains a TAB (i.e. not
  tab-separated).
- **FR-005**: The system MUST reject, with a clear and specific message, an empty input or an input
  that yields zero usable cards.
- **FR-006**: The system MUST reject, with a clear and specific message stating the limit, an input
  larger than an operator-configured maximum file size.
- **FR-007**: The system MUST reject, with a clear and specific message stating the limit, an input
  containing more cards than an operator-configured maximum.
- **FR-008**: The system MUST skip (and count) rows whose Back field is empty, as well as data rows
  that contain no TAB; such rows MUST NOT produce a card. A row with an empty Front but a non-empty
  Back MUST still produce a usable card.

**Speech & content fidelity**

- **FR-009**: The system MUST generate clear, natural, native-accent English speech for the Back
  (answer) sentence of every usable card.
- **FR-010**: The system MUST generate audio only for the Back (answer) side, never for the Front
  (prompt) side.
- **FR-011**: Before voicing, the system MUST produce clean spoken text by decoding HTML entities and
  removing CSV-style surrounding quotes from the Back field.
- **FR-012**: The system MUST preserve the user's original card text exactly (byte-for-byte) for
  display; it MUST NOT rewrite, reword, or alter the user's field text. Audio is added, nothing else
  changes.

**Packaging & playback behavior**

- **FR-013**: The system MUST produce an Anki package that imports into Anki without errors.
- **FR-014**: For each usable card, the imported package MUST display the original answer text and
  MUST automatically play that card's audio when the answer is revealed.
- **FR-015**: Each card MUST provide a replay control that plays the same audio again on demand.
- **FR-016**: The package MUST include the generated audio for every usable card as bundled media so
  playback works after import without any further download.
- **FR-031**: The output deck name and delivered package filename MUST be derived from the user's
  original filename stem (e.g. `vocab.txt` → deck "vocab", file `vocab.apkg`), falling back to deck
  "AnkiVoice deck" / file `ankivoice.apkg` when no usable name is available.

**Queue, fairness & durability**

- **FR-017**: The system MUST process requests strictly one at a time, in arrival order; the next
  request's synthesis MUST NOT begin until the current request's synthesis has finished.
- **FR-018**: On receiving a valid file, the system MUST acknowledge receipt and tell the user their
  current position in line.
- **FR-019**: The system MUST allow the delivery/upload of a finished package to overlap with the
  synthesis of the next queued request, while still never running two syntheses at once.
- **FR-020**: The system MUST allow each user at most one active job (queued or in progress) at a
  time, and MUST tell a user who already has an active job that their deck is already being processed
  rather than starting a second job for them.
- **FR-021**: The system MUST persist job state durably so that, after a restart, queued and
  in-progress work resumes and is delivered without the user resending the file.

**Delivery, archive & cleanup**

- **FR-022**: On completion the system MUST send the package to the fixed operator-owned archive
  destination first, then to the requesting user.
- **FR-023**: The system MUST remove the delivered package and the job's working files only after
  BOTH the archive copy and the user copy have been sent successfully.
- **FR-024**: The system MUST remove every temporary and output file for a job on both the success
  path and the terminal-failure path (a processing failure — parse, synthesis, or packaging — that
  ends the job as failed), so that disk usage stays flat over time. (A delivery-upload failure is NOT
  a terminal failure; see FR-026.)
- **FR-025**: File removal MUST be scoped strictly to the job's own working area and outputs, and
  MUST NEVER remove anything outside that area.
- **FR-026**: If either delivery copy fails, the system MUST retain the package (not delete it) and
  retry the job when the service next restarts (resume); it MUST NOT auto-delete an undelivered
  package. v1 has no in-process delivery-retry loop.
- **FR-027**: On successful delivery the system MUST send the user a clear, friendly "your deck is
  ready" message.

**Resilience & privacy**

- **FR-028**: Under bursty or sequential load the system MUST keep running — queueing and slowing
  down — without crashing, exhausting memory, or filling the disk.
- **FR-029**: User content MUST only be sent to the requesting user and the operator archive
  destination; it MUST NOT be sent anywhere else.
- **FR-030**: Speech generation MUST happen locally on the server with no per-request external
  service cost and no user text leaving the server for synthesis.

### Key Entities *(include if feature involves data)*

- **Submission (Input File)**: The tab-separated export a user sends. Attributes: originating user,
  received file, size. Validated into a set of usable cards or rejected with a reason.
- **Card**: One usable row derived from the input. Attributes: original Front text (preserved),
  original Back text (preserved, displayed), cleaned spoken text (derived from Back, not displayed),
  and the generated audio for the Back sentence.
- **Job**: One unit of work for one Submission by one user. Attributes: owning user, arrival order /
  queue position, lifecycle state (queued → synthesizing → packaging → uploading → delivered →
  cleaned, or failed), and a scoped working area on the server. At most one active Job per user; at
  most one Job synthesizing at a time.
- **Deck Package (Output)**: The Anki package produced for a Job — the importable file containing the
  cards (original text) and the bundled audio media, delivered to the archive and the user.
- **Archive Destination**: A fixed operator-owned location that receives a backup copy of every
  delivered package.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a valid export, 100% of usable cards in the delivered package auto-play correct
  native-accent audio of the answer sentence when the answer is revealed, and every card's replay
  control plays that audio again.
- **SC-002**: The delivered package imports into Anki with zero import errors for every valid export.
- **SC-003**: 100% of cards display the user's original answer text byte-for-byte unchanged (audio is
  added; no text is rewritten).
- **SC-004**: When two files arrive within the same few seconds, the second deck's synthesis begins
  only after the first deck's synthesis finishes, and at no point are two syntheses running at once.
- **SC-005**: Each sender receives a queue-position acknowledgement promptly after sending a valid
  file (target: within ~5 seconds under normal conditions).
- **SC-006**: After every delivery, zero per-job files remain on the server, and total disk usage
  after processing N decks is effectively the same as before processing them (it does not grow with
  N).
- **SC-007**: Under a burst of sequential submissions the service completes all of them by queueing,
  with zero out-of-memory or disk-full failures and zero crashes.
- **SC-008**: 100% of packages delivered to users are also present in the operator archive
  destination.
- **SC-009**: 100% of invalid inputs (wrong format, empty/zero usable cards, too large, too many
  cards) receive a specific, friendly, actionable error message, and the service remains running with
  no residual files from the rejected request.
- **SC-010**: After a restart that occurs while a deck is queued or mid-processing, that deck is still
  delivered without the user resending it.

## Assumptions

- **Input format**: Input is the standard Anki tab-separated text export with a Front column then a
  Back column; the Back field is the complete, correct English sentence to be spoken. Additional
  trailing columns/tags, if present, are not required for the feature and are not voiced.
- **Voiced side**: Audio is generated for the answer (Back) side only.
- **Voice**: There is one operator-configured default American-English voice. Users cannot choose a
  voice in v1 (no in-chat voice/accent picker).
- **Limits**: The per-job maximum card count and the maximum input file size are operator-configured
  limits; "too large" and "too many cards" are defined by these limits. Sensible defaults are
  provided so the service is safe on a tiny host.
- **One active job per user**: A user who already has an active (queued or in-progress) job and sends
  another file is told a deck is already being processed and asked to wait; the new file is declined
  rather than creating a second active job.
- **Empty Back rows**: Rows with an empty Back (or no TAB) are skipped and counted; a file with zero
  usable cards is treated as an invalid/empty input. A row with an empty Front but a valid Back is a
  usable card.
- **Encoding**: The uploaded file is decoded as UTF-8 (the Anki text-export encoding); undecodable
  input is rejected as wrong-format.
- **Output naming**: The deck name and delivered `.apkg` filename are derived from the user's original
  filename stem, with a generic fallback when unavailable.
- **Delivery ordering & retry**: The archive copy is sent before the user copy. If either copy fails,
  the package is retained for retry/resume; cleanup happens once delivery is fully complete, and on a
  terminal failure the job's own scoped files are still removed.
- **Privacy boundary**: The only outbound destinations for user content are the requesting user and
  the operator archive destination; nothing else leaves the server.
- **Channel**: Interaction is exclusively through the chat bot. There is no web UI, no accounts, and
  no payments in v1.
- **Connectivity**: The user has normal connectivity to the chat platform to send a file and receive
  the package; the server has whatever local capability it needs to generate speech offline.

## Out of Scope (v1)

- GPU acceleration and any paid cloud speech service.
- An in-chat voice or accent picker (one operator-set default voice only).
- A web UI, user accounts, or payments (chat bot only).
- Any editing of card text beyond adding audio (no rewording, correction, or reformatting).
