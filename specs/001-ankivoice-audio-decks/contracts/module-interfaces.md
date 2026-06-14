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

def load_config(env: Mapping[str, str] = os.environ) -> Config: ...
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
    spoken: str         # cleaned text for synthesis only (never displayed)

@dataclass(frozen=True)
class ParsedDeck:
    cards: list[Card]
    skipped_empty_back: int

class JobState(str, Enum):
    QUEUED="queued"; SYNTHESIZING="synthesizing"; PACKAGING="packaging"
    UPLOADING="uploading"; DELIVERED="delivered"; CLEANED="cleaned"; FAILED="failed"

@dataclass
class Job:
    id: int; user_id: int; chat_id: int
    input_path: str; original_filename: str | None
    state: JobState; error_reason: str | None
    created_at: str; updated_at: str
```

## `parser.py` — deck parsing/validation (load-bearing)

```python
def clean_for_speech(field: str) -> str: ...
    # html.unescape + strip one layer of CSV-style surrounding double-quotes
    # (and un-double internal "" -> "). Does NOT mutate display text. (FR-011)

def parse_deck(raw: bytes, *, max_cards: int) -> ParsedDeck: ...
    # Decodes UTF-8; skips leading '#'-prefixed header lines (FR-002);
    # splits each data row on TAB into Front, Back (FR-003);
    # builds Card(front, back, spoken=clean_for_speech(back));
    # skips + counts rows with empty Back (FR-008);
    # raises ValidationError(EMPTY) if zero usable cards (FR-005);
    # raises ValidationError(TOO_MANY_CARDS) if len(cards) > max_cards (FR-007);
    # raises ValidationError(WRONG_FORMAT) if no row has a TAB / structure unusable (FR-004).
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
```

Tests inject a `FakeSynthesizer` (deterministic samples, no model) so the default suite is offline
(Constitution P7).

## `audio.py` — MP3 encoding (pure)

```python
def encode_mp3(samples: FloatArray, sample_rate: int, out_path: Path,
               *, quality: str) -> Path: ...
    # Encodes mono float32 samples to an MP3 at out_path (approach pinned in research.md).
    # Deterministic; no network.
```

## `packaging.py` — Anki packager (load-bearing)

```python
@dataclass(frozen=True)
class MediaCard:
    front: str; back: str; audio_filename: str   # bare filename used in [sound:...]

def build_apkg(cards: Sequence[MediaCard], media_paths: Sequence[Path],
               out_path: Path, *, deck_name: str) -> Path: ...
    # Builds a deterministic-id note type whose ANSWER template shows the original Back
    # plus [sound:<audio_filename>] (auto-play on reveal + replay button; FR-013..016),
    # attaches media_paths, writes the .apkg to out_path, returns out_path.
```

## `store.py` — durable SQLite job store + state machine (load-bearing)

```python
class JobStore:
    def __init__(self, db_path: Path): ...        # creates schema if absent; WAL mode
    def has_active_job(self, user_id: int) -> bool: ...                 # FR-020
    def enqueue(self, *, user_id, chat_id, input_path, original_filename) -> Job: ...
    def queue_position(self, job_id: int) -> int: ...                   # FR-018
    def claim_next(self) -> Job | None: ...        # smallest-id QUEUED -> SYNTHESIZING (FCFS, FR-017)
    def set_state(self, job_id: int, state: JobState, *, error_reason: str | None = None) -> None: ...
    def get(self, job_id: int) -> Job | None: ...
    def requeue_in_progress(self) -> int: ...      # startup: non-terminal in-progress -> QUEUED (FR-021)
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
    # 1) send_document to archive_chat_id (FR-022)
    # 2) send_document to job.chat_id, then a friendly "ready" message (FR-027)
    # 3) only after BOTH succeed: set_state(DELIVERED) -> remove_job_dir -> set_state(CLEANED) (FR-023)
    # On any upload failure: do NOT delete; leave job for resume (FR-026). (Cleanup of the job dir on
    # a TERMINAL failure path is handled by the worker, still scoped via remove_job_dir.)
```

## `worker.py` — the single speech worker (load-bearing)

```python
class Worker:
    def __init__(self, *, store, synthesizer, sender, config): ...
    async def run(self, stop: asyncio.Event) -> None: ...
        # loop: job = store.claim_next(); if none, sleep briefly and continue.
        # SYNTHESIZE (one at a time, CPU work via asyncio.to_thread): parse input -> per-card MP3s
        #   (dedupe identical spoken via sha256 cache) ; PACKAGE -> .apkg.
        # Then dispatch deliver(...) as a SEPARATE asyncio task so delivery overlaps the next
        #   job's synthesis (FR-019), while only ONE synthesis runs at a time (FR-017).
        # On ValidationError or failure: set_state(FAILED, reason), notify user, scoped-clean job dir.
```

## `bot.py` — Telegram handlers + Sender impl

```python
def build_application(config: Config, store: JobStore) -> Application: ...
    # document handler: reject > max_file_bytes (TOO_LARGE); else if user has active job, decline
    #   (FR-020); else save upload into a new job dir, enqueue, reply queue position (FR-018).
    # /start, /help: usage text. Provides a TelegramSender implementing delivery.Sender.
    # post_init starts Worker.run(stop); post_shutdown signals stop + cancels.
```

## `__main__.py` — entrypoint

```python
def main() -> None: ...
    # load_config -> JobStore(db).requeue_in_progress() -> build KokoroSynthesizer ->
    # build_application -> app.run_polling()
```
