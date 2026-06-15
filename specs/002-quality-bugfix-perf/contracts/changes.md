# Contract Changes: cycle 002 (deltas to the 001 module interfaces)

These are the exact interface/behavior deltas. The 001
[module-interfaces.md](../../001-ankivoice-audio-decks/contracts/module-interfaces.md) is updated in
lockstep so it matches the code (IR-019). New/changed signatures only.

## parser.py

```python
def clean_for_speech(field: str) -> str: ...
    # html.unescape AFTER unwrapping a *balanced* transport-quoted field (surrounding "…" with
    # internal "" un-doubled). Non-wrapped fields pass through unchanged. (FR-011; matches the
    # contract that previously over-claimed plain CSV stripping.)

def parse_deck(raw: bytes, *, max_cards: int) -> ParsedDeck: ...
    # decode utf-8-sig (strips a leading BOM; raises WRONG_FORMAT on non-UTF-8);
    # normalize line endings to \n; skip the leading '#'-header block;
    # split LINE BY LINE on the FIRST TAB (rows never merge — one card per usable row, FR-003/FR-008);
    # display front/back = balanced-unwrap(field); spoken = clean_for_speech(back);
    # a row is usable iff spoken.strip() != "" (blank-after-clean is skipped+counted, FR-008);
    # raises WRONG_FORMAT (no TAB anywhere / undecodable), EMPTY (tabs but zero usable),
    # TOO_MANY_CARDS (> max).
```

## audio.py

```python
def encode_mp3(samples, sample_rate, out_path, *, quality, timeout: float = 120.0) -> Path: ...
    # ffmpeg path resolved ONCE (memoized); subprocess.run(..., timeout=timeout) raises a clear
    # RuntimeError on TimeoutExpired so a stuck encoder cannot hang the single worker (IR-018).
```

## speech.py

```python
class KokoroSynthesizer:
    def synthesize(self, spoken_text: str) -> FloatArray: ...
        # consumes the Kokoro generator inside `with torch.inference_mode():` — byte-identical PCM,
        # slightly less per-sentence overhead (IR-016, perf).
```

## packaging.py

```python
def build_apkg(cards, media_paths, out_path, *, deck_name) -> Path: ...
    # asserts every card.audio_filename basename matches a media_paths basename (E1);
    # sets tempfile.tempdir to out_path.parent for the duration of write_to_file so genanki's temp DB
    # lands inside the job dir and is scoped-cleaned (IR-012, B1). guid scheme unchanged:
    # guid_for(deck_name, str(index), front, back, audio_filename) — distinct rows + stable re-import.
```

## pipeline.py

```python
def build_package(...) -> Path: ...
    # per-sentence MP3 filename = sha256(spoken).hexdigest() + ".mp3" (FULL digest, no truncation —
    # removes the prefix-collision overwrite, A3/D4). Per-job dedupe on full spoken string unchanged.
```

## models.py

```python
class JobState(str, Enum):
    QUEUED="queued"; SYNTHESIZING="synthesizing"; UPLOADING="uploading"
    DELIVERED="delivered"; CLEANED="cleaned"; FAILED="failed"     # PACKAGING removed (D7)

@dataclass
class Job:
    ...                          # existing fields, plus:
    archive_sent: bool           # delivery idempotency flags (D8)
    user_sent: bool
```

## store.py

```python
class JobStore:
    def enqueue_if_no_active(self, *, user_id, chat_id, input_path, original_filename) -> Job | None: ...
        # ATOMIC: one transaction; returns None if the user already has an active job (FR-020, D9).
    def set_delivery_flag(self, job_id: int, *, archive: bool | None = None,
                          user: bool | None = None) -> None: ...           # D8
    def prune_terminal_jobs(self, *, keep: int) -> int: ...                # D10; returns #deleted
    # requeue_in_progress(): rebuildable = {SYNTHESIZING, UPLOADING} + legacy 'packaging'; never resets
    #   the delivery flags. Active/Ahead sets updated (no PACKAGING). (D7)
```

## delivery.py

```python
async def deliver(job, apkg_path, *, sender, store, archive_chat_id, work_root) -> None: ...
    # set UPLOADING; if not job.archive_sent: send_document(archive) then set_delivery_flag(archive=True);
    # if not job.user_sent: send_document(user) then set_delivery_flag(user=True);
    # once BOTH flags set: set_state(DELIVERED) -> ready message (best-effort) -> remove_job_dir ->
    # set_state(CLEANED). Idempotent: a re-run after a crash sends only the missing copy (IR-014).
    # (Doc ordering corrected: ready message is AFTER DELIVERED, best-effort, before cleanup.)
```

## worker.py

```python
class Worker:
    async def _process(self, job): ...
        # after build_package returns: store.set_state(job.id, UPLOADING) SYNCHRONOUSLY (was PACKAGING),
        # then dispatch _deliver as a separate task (delivery overlaps next synthesis).
    async def _deliver(self, job, apkg_path): ...
        # bounded retry: up to config.delivery_retries attempts with exponential backoff around deliver();
        # on final failure, retain (do not delete) for restart-resume (IR-015).
    async def resume(self): ...
        # prune_terminal_jobs(keep=config.job_history); requeue_in_progress (legacy 'packaging' too);
        # clean DELIVERED-but-uncleaned; fail abandoned uploads.
```

## bot.py

```python
def build_application(config: Config, store: JobStore, synthesizer) -> Application: ...
    # (signature corrected in the 001 contract: it always took `synthesizer`.)
    # on_document: store.enqueue_if_no_active(...); if None after saving the upload, delete the orphan
    #   job dir and message the user (D9).
```

## preflight.py (NEW)

```python
class PreflightError(Exception): ...

def check_runtime(config: Config) -> None: ...
    # Raise PreflightError naming the first missing of: espeak-ng (PATH), ffmpeg (PATH),
    # configured Kokoro weights + configured voice available offline (one-word real synth, also prewarms).
    # No-op iff ANKIVOICE_SKIP_PREFLIGHT is set. Called by __main__.main() before run_polling. (IR-008..011)
```

## __main__.py

```python
def main() -> None: ...
    # load_config -> set offline env -> preflight.check_runtime(config) -> JobStore(...) ->
    #   build KokoroSynthesizer (reused by preflight prewarm) -> build_application -> run_polling.
```

## bot-interface (user-facing) deltas

- On a refused second active job *after* a download race: the orphan is cleaned and the user gets the
  same "you already have a deck being processed" message (no change in wording; behavior hardened).
- No new user-visible messages otherwise; delivery retry is transparent (the user still gets exactly one
  package + one ready message).
