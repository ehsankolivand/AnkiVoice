# Bot Interaction Contract (user-facing)

The only external interface is the chat bot. Long-polling; no public TLS required.

## Commands

| Input | Bot response |
|-------|--------------|
| `/start`, `/help` | Friendly explanation: send a tab-separated Anki export (Front⇥Back, Back = full English sentence); the bot returns an audio-enhanced `.apkg`. States the file-size and card limits. |
| A document upload | See "Upload handling" below. |
| Any other text | Short hint pointing to `/help`. |

## Upload handling (a document message)

Decision order (first match wins):

1. **Too large** — `document.file_size > MAX_FILE_BYTES`
   → reply: *"That file is too large (limit: N MB). Please send a smaller export."* No job created.
   (FR-006, SC-009)
2. **User already has an active job**
   → reply: *"You already have a deck being processed. I'll get to one at a time — please wait for it
   to finish before sending another."* No job created. (FR-020)
3. **Accepted** — save the upload into a fresh job working dir, enqueue, reply with queue position:
   → *"Got it! Your deck is #K in line. I'll send it back when it's ready."* (FR-018, SC-005)

Content validation (wrong format / empty / too many cards) happens when the worker parses the file.
On a validation error the user gets the specific friendly message and the job ends as `failed`:

| Code | Message (example) |
|------|-------------------|
| `WRONG_FORMAT` | *"I couldn't read that as a tab-separated Anki export. Each line should be `Front⇥Back` (a TAB between the two columns)."* (FR-004) |
| `EMPTY` | *"That file has no usable cards — every row needs a Back (answer) sentence."* (FR-005, FR-008) |
| `TOO_MANY_CARDS` | *"That deck has too many cards (limit: N). Please split it into smaller decks."* (FR-007) |

## Result delivery

On success the user receives, in order:

1. The `.apkg` document (after the archive copy has already been sent — FR-022).
2. A friendly ready message: *"✅ Your audio deck is ready! Import it into Anki — each answer will
   play its audio automatically, with a replay button."* (FR-027)

## Guarantees surfaced to the user

- Strictly one deck synthesized at a time, in arrival order (FR-017).
- Original card text unchanged; audio added only (FR-012).
- The bot keeps running under load — extra files queue and wait (FR-028).

## Notes / limits (from research.md)

- Bot API limits: incoming file download is capped (~20 MB) and bot uploads (~50 MB);
  `MAX_FILE_BYTES` is configured at or below the download cap. A deck export is plain text, so this
  is generous; the produced `.apkg` (with MP3s) must stay under the upload cap (bounded by
  `MAX_CARDS`).
