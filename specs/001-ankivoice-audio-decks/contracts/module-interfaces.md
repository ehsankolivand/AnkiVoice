# Internal Module Contracts

Each module is one self-contained responsibility (Constitution P2). These are the public interfaces
other modules depend on; everything else is private. Signatures are the contract — tests target them.
All paths are absolute and scoped to a job's working dir.

## `config.py` — configuration (P8)

```python
@dataclass(frozen=True)
class Config:
    bot_token: str
    archive_chat_id: int
    default_voice: str
    lang_code: str            # e.g. "a" (American English)
    max_cards: int
    max_file_bytes: int
    work_dir: Path
    db_path: Path
    model_dir: Path | None    # local model/voice cache for offline use
    sample_rate: int          # confirmed in research.md
    mp3_quality: str          # encoder setting (research.md)
    voice_sides: str          # "back" (default — voice the Back only) | "both" (also voice the Front)
    # (cycle-002 operational limits job_history/ffmpeg_timeout/delivery_retries also present — see 002)

def load_config(env: Mapping[str, str] = os.environ) -> Config: ...
    # ANKIVOICE_VOICE_SIDES is normalized case-insensitively to "back"|"both"; anything else -> ConfigError.
    # Reads ANKIVOICE_* keys. Raises ConfigError listing every missing required key.
    # NEVER hard-codes secrets. Optionally loads a .env file first.
```

## `errors.py` — typed, friendly errors

```python
class ValidationError(Exception):
    code: str           # WRONG_FORMAT | EMPTY | TOO_LARGE | TOO_MANY_CARDS
    user_message: str   # friendly, actionable text shown to the user (FR-004..007, FR-009 SC-009)
```

## `models.py` — shared value types

```python
@dataclass(frozen=True)
class Card:
    front: str          # original, preserved (display)
    back: str           # original, preserved (display)
    spoken: str         # cleaned Back text for synthesis only (never displayed)
    front_spoken: str = ""   # cleaned Front text for synthesis (both-sides mode); "" ⇒ no Front audio

@dataclass(frozen=True)
class ParsedDeck:
    cards: list[Card]
    skipped_empty_back: int

class JobState(str, Enum):                      # cycle 002: PACKAGING removed (never observable)
    QUEUED="queued"; SYNTHESIZING="synthesizing"
    UPLOADING="uploading"; DELIVERED="delivered"; CLEANED="cleaned"; FAILED="failed"

@dataclass
class Job:
    id: int; user_id: int; chat_id: int
    input_path: str; original_filename: str | None
    state: JobState; error_reason: str | None
    created_at: str; updated_at: str
    archive_sent: bool = False; user_sent: bool = False   # cycle 002: delivery idempotency flags
```

## `parser.py` — deck parsing/validation (load-bearing)

```python
def clean_for_speech(field: str) -> str: ...
    # Unwrap a field's CSV transport quoting ONLY when it is a complete balanced quoted field
    # (surrounding "…" with internal "" un-doubled), THEN html.unescape. Non-balanced fields pass
    # through unchanged. Does NOT alter display text beyond this transport decoding. (FR-011)

def parse_deck(raw: bytes, *, max_cards: int) -> ParsedDeck: ...
    # Decodes utf-8-sig (strips a leading BOM; raises ValidationError(WRONG_FORMAT) on undecodable
    #   bytes, FR-004); normalizes \r\n / lone \r to \n; skips the leading '#'-header block (FR-002);
    # parses LINE BY LINE (rows never merge) — splits each data line into tab-separated fields and
    #   takes the first two as Front, Back; extra fields ignored (FR-003);
    # display front/back = balanced-unwrap(field); Front MAY be empty;
    # spoken = clean_for_speech(back); front_spoken = clean_for_speech(front) (both-sides mode; empty/
    #   whitespace ⇒ no Front audio); a row is USABLE iff spoken.strip() != "" (FR-003, FR-008);
    # skips + counts rows with empty Back, no TAB, OR a Back that cleans to whitespace (FR-008);
    # raises ValidationError(WRONG_FORMAT) if NO data row contains a TAB (not tab-separated, FR-004);
    # raises ValidationError(EMPTY) if TABs exist but zero usable cards remain (FR-005);
    # raises ValidationError(TOO_MANY_CARDS) if len(cards) > max_cards (FR-007).
```

## `speech.py` — speech synthesis wrapper (load-bearing)

```python
class Synthesizer(Protocol):
    sample_rate: int
    def synthesize(self, spoken_text: str) -> "FloatArray": ...
        # returns mono float32 PCM samples for ONE sentence. Pure w.r.t. text.

class KokoroSynthesizer:                       # the real, local, offline implementation
    def __init__(self, *, voice: str, lang_code: str,
                 model_dir: Path | None = None): ...   # loads the model ONCE, CPU-only
    sample_rate: int
    def synthesize(self, spoken_text: str) -> FloatArray: ...
        # cycle 002: runs inside torch.inference_mode() (byte-output-neutral; less per-sentence overhead)
```

Tests inject a `FakeSynthesizer` (deterministic samples, no model) so the default suite is offline
(Constitution P7).

## `audio.py` — MP3 encoding (pure)

```python
def encode_mp3(samples: FloatArray, sample_rate: int, out_path: Path,
               *, quality: str, timeout: float = 120.0) -> Path: ...
    # Encodes mono float32 samples to an MP3 at out_path (ffmpeg+libmp3lame via stdin; research.md).
    # ffmpeg path resolved ONCE (memoized). Raises RuntimeError if ffmpeg missing, the encode fails,
    # or it exceeds `timeout` seconds (a stuck encoder must not hang the worker — cycle 002). No network.
```

## `packaging.py` — Anki packager (load-bearing)

```python
@dataclass(frozen=True)
class MediaCard:
    front: str; back: str; audio_filename: str    # bare filename used in the Back [sound:...]
    front_audio_filename: str | None = None        # Front [sound:...] (both mode); None ⇒ no Front audio

def build_apkg(cards: Sequence[MediaCard], media_paths: Sequence[Path],
               out_path: Path, *, deck_name: str, voice_sides: str = "back") -> Path: ...
    # back mode (default): deterministic-id 3-field note type whose ANSWER template shows the original
    #   Back plus [sound:<audio_filename>] (auto-play on reveal + replay button; FR-013..016).
    # both mode: a SECOND, distinct deterministic-id 4-field note type (adds FrontAudio); the QUESTION
    #   template shows the Front plus [sound:<front_audio_filename>] (auto-play on the front + replay),
    #   the ANSWER template is unchanged — the front arrives via {{FrontSide}}, which Anki does NOT
    #   auto-replay (no re-blast; replay button still shown). Empty FrontAudio ⇒ no front [sound:].
    # attaches media_paths, writes the .apkg to out_path, returns out_path.
    # deck_name and out_path stem derive from the user's original filename stem, with a
    # generic fallback ("AnkiVoice deck" / "ankivoice.apkg") when unavailable (FR-031).
    # An empty Front is replaced by a neutral placeholder so the card is still generated/studyable
    # (an Anki card with an empty question side is not created). Per-note guid = guid_for(deck_name,
    # str(index), front, back, audio_filename) so identical export rows stay distinct AND re-importing
    # the same file updates rather than duplicates.
    # Cycle 002: asserts every card's [sound:] basename has a matching media path (raises ValueError
    # otherwise); writes genanki's temp DB INSIDE out_path's job dir (tempfile.tempdir override,
    # restored) so scoped cleanup removes it and disk stays flat.

def output_name(original_filename: str | None) -> str: ...
    # -> safe deck/file base name from the original filename stem, else "AnkiVoice deck" (FR-031).
```

## `pipeline.py` — synchronous core: parse → synth → encode → package (load-bearing)

```python
def build_package(deck_bytes: bytes, synthesizer: Synthesizer, *, job_dir: Path,
                  max_cards: int, deck_name: str, mp3_quality: str,
                  voice_sides: str = "back") -> Path: ...
    # parse_deck(deck_bytes, max_cards) -> for each UNIQUE spoken (dedupe by FULL sha256) call
    # synthesizer.synthesize + audio.encode_mp3 into job_dir (identical sentences synthesize once). In
    # both mode the Front is also voiced and the SAME cache spans both sides (a Front equal to some Back
    # synthesizes once); an empty/whitespace Front is not voiced;
    # build_apkg(cards, media_paths, out=job_dir/<name>.apkg, deck_name, voice_sides) -> apkg path.
    # Pure/synchronous (CPU-bound) — the worker runs it via asyncio.to_thread. Raises ValidationError
    # (propagated from the parser) for bad input. (FR-009..016, per-job dedupe = Constitution P1.)
```

## `store.py` — durable SQLite job store + state machine (load-bearing)

```python
class JobStore:
    def __init__(self, db_path: Path): ...        # creates schema if absent; WAL mode; additive migration
    def has_active_job(self, user_id: int) -> bool: ...                 # FR-020
    def enqueue(self, *, user_id, chat_id, input_path, original_filename) -> Job: ...
    def enqueue_if_no_active(self, *, user_id, chat_id, input_path,
                             original_filename) -> Job | None: ...      # ATOMIC reserve; None if active (FR-020, cycle 002)
    def queue_position(self, job_id: int) -> int: ...                   # ahead = {QUEUED, SYNTHESIZING} (FR-018)
    def claim_next(self) -> Job | None: ...        # smallest-id QUEUED -> SYNTHESIZING (FCFS, FR-017)
    def set_state(self, job_id: int, state: JobState, *, error_reason: str | None = None) -> None: ...
    def set_delivery_flag(self, job_id: int, *, archive: bool | None = None,
                          user: bool | None = None) -> None: ...        # delivery idempotency (cycle 002)
    def get(self, job_id: int) -> Job | None: ...
    def requeue_in_progress(self) -> int: ...      # startup: {SYNTHESIZING,UPLOADING,legacy 'packaging'} -> QUEUED, flags kept (FR-021)
    def prune_terminal_jobs(self, *, keep: int) -> int: ...             # bound the table (cycle 002)
    def list_active(self) -> list[Job]: ...        # observability
```

## `cleanup.py` — scoped deletion (load-bearing guarantee, Principle V)

```python
def remove_job_dir(job_dir: Path, *, work_root: Path) -> None: ...
    # Asserts job_dir is strictly inside work_root (resolved, no symlink escape);
    # raises if not. Then removes job_dir recursively. NEVER deletes anything else. (FR-024,025)
```

## `delivery.py` — delivery + cleanup orchestration (load-bearing)

```python
class Sender(Protocol):                            # implemented by the Telegram layer; faked in tests
    async def send_document(self, chat_id: int, path: Path, *, filename: str, caption: str|None=None) -> None: ...
    async def send_message(self, chat_id: int, text: str) -> None: ...

async def deliver(job: Job, apkg_path: Path, *, sender: Sender, store: JobStore,
                  archive_chat_id: int, work_root: Path) -> None: ...
    # set_state(UPLOADING); then IDEMPOTENT per-copy (cycle 002, exactly-once):
    # 1) if not archive_sent: send_document(archive_chat_id) -> set_delivery_flag(archive=True) (FR-022)
    # 2) if not user_sent: send_document(job.chat_id) -> set_delivery_flag(user=True)
    # 3) once BOTH flags set: set_state(DELIVERED) -> friendly "ready" message (best-effort, AFTER
    #    DELIVERED) -> remove_job_dir -> set_state(CLEANED) (FR-023, FR-027)
    # On any upload failure: do NOT delete; leave job for resume (FR-026). A re-run (retry or restart)
    # sends ONLY the copy not yet sent. (Terminal-failure cleanup is handled by the worker, scoped.)
```

## `worker.py` — the single speech worker (load-bearing)

```python
class Worker:
    def __init__(self, *, store, synthesizer, sender, config,
                 delivery_backoff_base: float = 0.5): ...
    async def resume(self) -> None: ...
        # startup: prune_terminal_jobs(keep=config.job_history); requeue_in_progress (legacy 'packaging'
        #   too, flags kept); clean DELIVERED-but-uncleaned; fail abandoned uploads. (FR-021, cycle 002)
    async def run(self, stop: asyncio.Event) -> None: ...
        # await resume(); then loop: job = store.claim_next(); if none, sleep briefly and continue.
        # SYNTHESIZE (one at a time, CPU work via asyncio.to_thread): parse input -> per-card MP3s
        #   (dedupe identical spoken via FULL sha256 cache) -> build .apkg.  Then set_state(UPLOADING)
        #   SYNCHRONOUSLY (cycle 002: no PACKAGING) and dispatch deliver(...) as a SEPARATE asyncio task
        #   so delivery overlaps the next job's synthesis (FR-019), while only ONE synthesis runs (FR-017).
        # Delivery uses a BOUNDED retry (config.delivery_retries, exp backoff) then retains for resume.
        # On ValidationError or failure: set_state(FAILED, reason), notify user, scoped-clean job dir.
```

## `bot.py` — Telegram handlers + Sender impl

```python
def build_application(config: Config, store: JobStore, synthesizer) -> Application: ...
    # document handler: reject > max_file_bytes (TOO_LARGE); else store.enqueue_if_no_active (ATOMIC
    #   reserve-before-download) — None => decline (FR-020); else save upload into the reserved job dir,
    #   mark claimable, reply queue position (FR-018); download failure => scoped-clean + notify.
    # /start, /help: usage text. Provides a TelegramSender implementing delivery.Sender.
    # post_init starts Worker.run(stop) (which calls resume()); post_shutdown signals stop + cancels.
```

## `preflight.py` — fail-fast startup guard (cycle 002, correctness guard)

```python
class PreflightError(Exception): ...

def check_runtime(config: Config, synthesizer) -> None: ...
    # Raise PreflightError if ffmpeg is not on PATH, OR the configured voice/model + phonemizer cannot
    # synthesize a one-word OUT-OF-DICTIONARY probe offline (which also PREWARMS the model). espeak-ng is
    # BUNDLED (espeakng_loader, in-process) — NOT gated on PATH (that would false-positive); the probe is
    # the ground truth. No-op if ANKIVOICE_SKIP_PREFLIGHT is set. Called by __main__ before run_polling.
```

## `__main__.py` — entrypoint

```python
def main() -> None: ...
    # load_config -> set offline env (unless ANKIVOICE_ALLOW_DOWNLOADS) -> JobStore(db) ->
    # build KokoroSynthesizer -> preflight.check_runtime(config, synth) [SystemExit on failure] ->
    # build_application(config, store, synth) -> app.run_polling()  (resume runs inside Worker.run)
```
