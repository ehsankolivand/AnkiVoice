# Research & Pinned Decisions: AnkiVoice

All findings below were **verified against the actually-installed packages** in the project's `uv`
environment (not from memory). Verification methods are noted per item. These versions and
identifiers are pinned for implementation (Constitution: verify-don't-guess).

## Pinned versions

| Package / tool | Version | License | Notes |
|---|---|---|---|
| Python | 3.12 (uv fetched 3.12.x) | PSF | `requires-python = ">=3.12,<3.13"` |
| kokoro | 0.9.4 | Apache-2.0 | TTS pipeline |
| misaki | 0.9.4 | Apache-2.0 | English G2P (used by kokoro) |
| torch | 2.12.0 | BSD-3-Clause | CPU backend |
| transformers | 5.12.0 | Apache-2.0 | pulled by kokoro |
| Kokoro-82M weights | `hexgrad/Kokoro-82M` (`kokoro-v1_0.pth`, ~327 MB) | Apache-2.0 | + voice packs `voices/*.pt`, Apache-2.0 |
| genanki | 0.13.1 | MIT | `.apkg` builder |
| python-telegram-bot[ext] | 22.8 | **LGPL-3.0-only** | async long-polling. Used unmodified as a library — LGPL imposes no obligation on our (separate) code. |
| soundfile | 0.14.0 (libsndfile 1.2.2) | BSD-3 / LGPL-2.1+ | used only to build an in-memory WAV buffer |
| numpy | 2.4.6 | BSD-3 | |
| ffmpeg | 8.1.1 + libmp3lame | LGPL/GPL (invoked as a subprocess → no linking obligation) | MP3 encoder; `apt install ffmpeg` on a VPS |
| espeak-ng | 1.52.0 | GPL-3.0 (separate binary on PATH) | required by misaki for robust English G2P |

Dev: pytest 9.1.0, pytest-asyncio 1.4.0, pytest-mock 3.15.1.

---

## Decision 1 — Speech: Kokoro-82M, CPU-only, offline (P1, P4)

**Decision**: Use `kokoro.KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M", device="cpu")` for
American English. Call it as a generator and concatenate the per-chunk audio:

```python
from kokoro import KPipeline
import numpy as np

pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M", device="cpu")  # load ONCE, reuse
chunks = [r.output.audio.detach().cpu().numpy()
          for r in pipeline(text, voice="af_heart", speed=1.0)]
audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]  # float32 mono
SAMPLE_RATE = 24000  # Hz, fixed (no public constant)
```

**Verified by**: real CPU synthesis of "Hello world, this is a Kokoro test." → `dtype=float32`,
`shape=(69000,)`, mono, range ≈ [-0.26, 0.36], `device=cpu`; re-ran with `HF_HUB_OFFLINE=1` → OK;
`inspect.signature` on `KPipeline.__init__/__call__`; listed 54 repo voices (20 American-English).

**Key facts**:
- American English = `lang_code="a"` (`"b"` = British). Default voice = **`af_heart`** (female AmE).
  20 AmE voice ids available (`af_*` female, `am_*` male).
- `__call__(text, voice=None, speed=1, split_pattern="\n+", model=None)` returns a **generator** of
  `Result`; `result.output.audio` is a `torch.FloatTensor` → `.detach().cpu().numpy()`.
- **Sample rate is fixed at 24000 Hz** (hard-coded in source; no constant — use the literal).
- Load the pipeline **once** and reuse (Constitution P1, P4).
- Force CPU with `device="cpu"`; also call `torch.set_num_threads(1)` to respect the single-core
  budget (P1). Pass `repo_id` explicitly to silence a stdout warning.

**Offline operation (P4)** — the one-time online warm-up before going offline must fetch:
1. the weights `kokoro-v1_0.pth` and `config.json`, 2. **each voice `.pt` you will use** (voices
download lazily *per id*), 3. the spaCy model `en_core_web_sm` (misaki English G2P installs it on
first English run). After warm-up, set `HF_HUB_OFFLINE=1` and synthesis runs fully offline (verified).
An uncached voice fails offline with `LocalEntryNotFoundError`. Cache dir:
`~/.cache/huggingface/hub/models--hexgrad--Kokoro-82M/...`; configurable via `ANKIVOICE_MODEL_DIR`
(→ `HF_HOME`). A `scripts/warmup.py` will perform the warm-up.

**espeak-ng is required**: misaki's `EspeakFallback` phonemizes out-of-dictionary words. **Without
espeak-ng those words are silently dropped from the audio** (verified). Keep `espeak-ng` installed and
on PATH (`apt install espeak-ng`). This is a deployment dependency, documented in the README.

**Gotchas**: `__call__` returns a generator (must iterate + concatenate); audio length varies per text
(never assume fixed length); tensor → numpy before use; not clamped to exactly ±1.

**Alternatives rejected**: GPU / paid cloud TTS (violates P1, P4). soundfile/other local TTS — Kokoro
is the brief's mandated engine and was confirmed to run on CPU offline.

---

## Decision 2 — MP3 encoding: ffmpeg + libmp3lame via stdin (P1, P4)

**Decision**: Encode Kokoro's float32 mono array to MP3 with an **ffmpeg subprocess** using
`libmp3lame`, feeding an in-memory WAV (PCM_16, built with soundfile) to ffmpeg's **stdin** (`pipe:0`)
so no temp WAV is written:

```
ffmpeg -y -hide_banner -loglevel error -i pipe:0 -ac 1 -codec:a libmp3lame -qscale:a 4 OUT.mp3
```

**Verified by**: encoded a 0.3 s 24 kHz float32 sine via this exact path → `ffprobe` confirmed
`codec=mp3, 24000 Hz, mono, 0.300 s` (~36 kbps VBR, ~1.6 KB). The soundfile-direct MP3 path also
worked *on this macOS host* but was **rejected** (see below).

**Key facts / rationale**:
- Resolve the binary with `shutil.which("ffmpeg")` and fail clearly if missing.
- `-ac 1` forces mono; `-qscale:a 4` is VBR tuned for clear speech at small size (use `-b:a 64k` for
  predictable CBR size). Pass `input=wav_bytes` to `subprocess.run` (binary-safe, no shell).
- Single short subprocess per sentence — low single-core cost; fully offline.

**Alternatives rejected**: `soundfile.write(..., format="MP3")` — works only when libsndfile was built
with MP3 (≥1.1.0, build-time optional); **stock Debian/Ubuntu libsndfile commonly lacks MP3 encode**,
so it would raise on the VPS. `pydub`/`lameenc` — extra deps; ffmpeg is the standard, well-packaged
choice. Writing a temp WAV first — unnecessary disk churn; stdin pipe avoids it.

---

## Decision 3 — Packaging: genanki, answer-side `[sound:]` auto-play + replay (FR-013..016)

**Decision**: One `genanki.Model` (fixed `model_id`), fields `Front`, `Back`, `Audio`; a single card
template whose **answer side (`afmt`) renders `{{Audio}}` where `Audio = "[sound:<bare>.mp3]"`** and the
**question side (`qfmt`) has no audio**. Anki auto-plays a `[sound:]` tag when the side containing it is
shown and auto-draws a replay button. Attach real files via `Package.media_files = [paths]`.

```python
MODEL_ID = 1607392319; DECK_ID = 2059400110   # chosen once, hard-coded (deterministic)
model = genanki.Model(MODEL_ID, "AnkiVoice Audio",
    fields=[{"name":"Front"}, {"name":"Back"}, {"name":"Audio"}],
    templates=[{"name":"Card 1",
        "qfmt":"{{Front}}",
        "afmt":"{{FrontSide}}<hr id=answer>{{Back}}<br>{{Audio}}"}],
    css=".card{font-family:arial;font-size:20px;text-align:center;}")
note = genanki.Note(model=model, fields=[front, back, "[sound:%s]" % audio_basename], guid=<stable>)
pkg = genanki.Package(deck); pkg.media_files = [abs_mp3_path, ...]; pkg.write_to_file(out)
```

**Verified by**: built a real 2-card deck referencing two real `.mp3` files, wrote the `.apkg`, and
`unzip -l` confirmed it contains `collection.anki2`, a `media` JSON map (`{"0":"card1_answer.mp3",...}`),
and numbered media files `0`,`1`. `inspect.signature` on Model/Note/Deck/Package/write_to_file; the
field-count guard was observed raising `ValueError`.

**Key facts / gotchas**:
- Inside `[sound:...]` use the **bare basename only**; the filesystem path goes in `media_files`; the
  basename must match. A path inside `[sound:...]` makes Anki show literal text instead of playing.
- Keep `[sound:]` **out of `qfmt`** (else it auto-plays on the front).
- Use **deterministic** `model_id`/`deck_id` and a **stable per-note `guid`** (e.g. derived from a
  deck+content hash) so re-imports update rather than duplicate.
- Note field count must equal Model field count (3 here).
- genanki 0.13.1 writes the legacy `collection.anki2` name — imports fine in modern Anki.
- Anki fields are HTML; the user's exported field is preserved verbatim (FR-012). We do **not**
  re-escape it (re-escaping would alter the displayed text); invalid HTML only triggers a genanki
  warning, not an error. (Per-card audio filenames are made unique/safe by us, not from user text.)

**Alternatives rejected**: hand-written `<audio>` element — loses Anki's native auto-play + replay
handling. Random ids — cause duplicate models/decks on re-import.

---

## Decision 4 — Telegram: python-telegram-bot 22.8, long-polling + one background worker

**Decision**: `ApplicationBuilder().token(...).post_init(start_worker).post_shutdown(stop_worker).build()`
then **`app.run_polling()`** (blocking; owns the event loop; called from a **sync** `main()`). Receive
uploads with `MessageHandler(filters.Document.ALL & ~filters.COMMAND, on_document)`. The single
synthesis worker is one long-lived asyncio task created with **`app.create_task(worker(...))` inside
`post_init`** (app is running → task is tracked/awaited) and cancelled in `post_shutdown`.

```python
async def on_document(update, context):
    doc = update.message.document
    if doc.file_size and doc.file_size > MAX_FILE_BYTES:   # check BEFORE get_file (Bot API ~20MB)
        return await update.message.reply_text("That file is too large …")
    f = await doc.get_file()                # async, no positional args
    await f.download_to_drive(dest_path)    # -> pathlib.Path
    ...
await context.bot.send_document(chat_id=cid, document=path, filename=name, caption=text,
                                write_timeout=120)   # keyword-only filename/timeouts
```

**Verified by**: imports of `ApplicationBuilder/Application/MessageHandler/CommandHandler/ContextTypes/
filters`; `inspect.signature` on `run_polling`, `ApplicationBuilder.token/post_init/post_shutdown`,
`Application.create_task`, `Document.get_file`, `File.download_to_drive`, `Bot.send_document`,
`Message.reply_text`; confirmed `build()` does no network I/O and `create_task` while not-running emits
a warning (→ use post_init). No bot token available, so live polling was not exercised.

**Key facts / gotchas (CRITICAL for this design)**:
- **CPU-bound synthesis must run via `asyncio.to_thread(...)`** — calling Kokoro directly in a handler
  or the worker coroutine would block the event loop and freeze the bot. The worker awaits one
  `to_thread` synthesis at a time → satisfies "exactly one synthesis at a time" (FR-017, P1).
- **Delivery overlap (FR-019)**: after the worker finishes synth+package for job A, it schedules
  `deliver(A)` as a **separate** `app.create_task(...)` (network I/O) and immediately loops to claim
  and synthesize job B. Only the worker performs synthesis, sequentially → overlap without concurrency.
- `filters.Document.ALL` is a ready **instance** (don't call it); combine with `& ~filters.COMMAND`.
- Bot API limits: **download ~20 MB** (check `file_size` first), **upload ~50 MB** (bump
  `write_timeout` for the `.apkg`). Long-polling needs **no public TLS / inbound port**.
- `run_polling()` is blocking and creates/owns the loop — call from sync `main()`, never `await` it.
- License **LGPL-3.0-only**: PTB is used unmodified as an installed dependency; this places no
  copyleft obligation on AnkiVoice's own source. (Recorded for the operator; no constitution conflict.)

**Alternatives rejected**: webhook mode (needs public TLS — unnecessary on a $6 VPS, brief prefers
long-polling); JobQueue for the worker (it's for scheduled jobs, not a persistent always-on loop).

---

## Concurrency & resource model (ties Decisions 1–4 to Constitution P1/P6)

- **Event-loop thread** runs PTB handlers, all SQLite operations (quick), and `deliver(...)` tasks.
  Single-threaded DB access (one connection, WAL, `busy_timeout`) → no cross-thread sqlite issues.
- **One worker coroutine** drives jobs FCFS; the heavy Kokoro+ffmpeg work runs in a **thread**
  (`asyncio.to_thread`) one job at a time. `torch.set_num_threads(1)` bounds CPU (P1).
- **Per-job dedupe cache**: within a deck, identical `spoken` strings (keyed by `sha256(spoken)`)
  synthesize once. Cache is per-job (lives in the job dir) → keeps disk flat (P5); no cross-job cache.
- **Restart-resume (FR-021, SC-010)**: on startup, non-terminal in-progress jobs are reset to
  `queued` and rebuilt from the persisted input file. Tradeoff: a crash *during* the upload step can
  produce one duplicate **archive** backup on resume — accepted as rare and harmless (the user still
  gets exactly one correct delivery; cleanup only runs after both uploads succeed).

## Resolved configuration defaults (operator-overridable; shipped in `.env.example`)

| Key | Default | Reason |
|---|---|---|
| `ANKIVOICE_DEFAULT_VOICE` | `af_heart` | Confirmed AmE default voice. |
| `ANKIVOICE_LANG_CODE` | `a` | American English. |
| `ANKIVOICE_MAX_CARDS` | `200` | Bounds per-deck synthesis time on one core (P1). |
| `ANKIVOICE_MAX_FILE_BYTES` | `2000000` (2 MB) | Text decks are tiny; well under the ~20 MB download cap. |
| `ANKIVOICE_WORK_DIR` | `./work` | Root for `job_<id>/` dirs (cleaned after delivery). |
| `ANKIVOICE_DB_PATH` | `./data/ankivoice.db` | The only datastore. |
| `ANKIVOICE_MODEL_DIR` | unset → HF default cache | Set `HF_HOME` for a pinned offline cache. |
| (fixed) sample rate | `24000` Hz | Kokoro output rate. |
| (fixed) MP3 args | `-ac 1 -codec:a libmp3lame -qscale:a 4` | Clear speech, small size. |
