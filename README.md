# AnkiVoice

A Telegram bot that turns a text-based Anki deck into an **audio-enhanced `.apkg`** with clear,
natural, native-accent English speech — generated **locally and offline** with
[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) on CPU. Built to run safely on a single-core,
~4 GB VPS.

Send the bot a tab-separated Anki export (`Front⇥Back`, where Back is the full answer sentence). It
returns an `.apkg` where **revealing a card's answer auto-plays its audio** and shows a replay button.
Your original card text is preserved exactly — audio is only added.

Built spec-first with [GitHub Spec Kit](https://github.com/github/spec-kit); the full
spec/plan/research/tasks live in [`specs/001-ankivoice-audio-decks/`](specs/001-ankivoice-audio-decks/),
governed by the [project constitution](.specify/memory/constitution.md).

## How it works (module map)

Small, single-responsibility modules under `src/ankivoice/` (Constitution P2 — agent-native):

| Module | Responsibility |
|--------|----------------|
| `config.py` | Load all settings from `ANKIVOICE_*` env vars (no hard-coded secrets). |
| `errors.py` | `ValidationError(code, user_message)` — friendly, actionable. |
| `models.py` | Shared types: `Card`, `ParsedDeck`, `Job`, `JobState`. |
| `parser.py` | Parse/validate the tab-separated export; clean text for speech. |
| `speech.py` | Kokoro wrapper — load once, CPU-only, offline. |
| `audio.py` | Encode samples to MP3 via an ffmpeg subprocess. |
| `packaging.py` | Build the `.apkg` (genanki) with answer-side `[sound:]` auto-play + replay. |
| `pipeline.py` | The synchronous core: parse → synth → encode → package (per-deck dedupe). |
| `store.py` | Durable SQLite job queue + state machine (FCFS, one-active-per-user, resume). |
| `cleanup.py` | Scoped deletion — only ever inside the work dir. |
| `delivery.py` | Deliver to archive → user, then scoped cleanup; retain on failure. |
| `worker.py` | The single synthesis worker (one at a time; delivery overlaps next synth). |
| `bot.py` | Telegram long-polling handlers + sender + worker wiring. |
| `preflight.py` | Fail-fast startup guard: refuses to start if espeak-ng/ffmpeg are missing or the configured voice/model is not cached offline. |
| `__main__.py` | Entrypoint. |

The flow: **ingest → synthesize → package → deliver**, serialized through the SQLite queue with
exactly one synthesis at a time; every job's files are cleaned up after delivery (success or failure).

## Deploy on a Debian/Ubuntu VPS (one command)

Go from a clean Debian 12 (bookworm) / Ubuntu LTS host to a running, auto-restarting,
boot-enabled bot with a single command. You only need two things: a **bot token** (from
[@BotFather](https://t.me/BotFather) — send `/newbot`, follow the prompts, copy the token) and an
**archive chat id** (a channel/group the bot is a member of, often negative like `-1001234567890`,
or your own numeric user id — the bot sends a backup copy of every delivered deck there).

```bash
# on the VPS, as root (or with sudo):
git clone <this-repo-url> ankivoice
cd ankivoice
sudo ./install.sh --token <BOT_TOKEN> --archive-id <ARCHIVE_CHAT_ID>
# …or run `sudo ./install.sh` with no value flags and it will prompt for the two values.
```

That single command: installs `ffmpeg` + `uv`, creates a dedicated `ankivoice` service user and a
`/opt/ankivoice` install dir, provisions the app (`uv sync`), does the one-time model warm-up so the
bot then runs **fully offline**, writes a `0600` `.env` (never overwriting an existing one), and
installs + enables + starts a `systemd` service whose startup self-check must pass. **Re-running it
is the supported way to update** — it refreshes code/deps and never touches your `.env`.

**System requirements**: Debian 12 / recent Ubuntu LTS, single CPU core, ~4 GB RAM, ~40 GB disk,
x86_64, outbound internet during install only (the running bot needs no inbound port, no TLS, no
reverse proxy — it uses outbound long-polling). Other distros are refused with a clear message.

**Operator commands** (copy-paste on the host):

```bash
journalctl -u ankivoice -f        # view live logs
systemctl status ankivoice        # status (active / enabled-on-boot)
systemctl restart ankivoice       # graceful restart (an in-flight deck finishes or safely resumes)
sudo ./install.sh                 # update (re-run; preserves your .env)
sudo ./uninstall.sh               # remove the service (app, data, and .env are kept)
sudo ./uninstall.sh --purge       # ALSO remove the install dir, data, model cache, and the user
```

Overridable defaults (env or flags): `ANKIVOICE_USER` / `--user` (default `ankivoice`),
`INSTALL_DIR` / `--prefix` (default `/opt/ankivoice`). Full deploy spec:
[`specs/003-one-command-deploy/`](specs/003-one-command-deploy/).

## Prerequisites (manual / development install)

- Python 3.12 and [`uv`](https://docs.astral.sh/uv/).
- One system package on PATH: **ffmpeg** (with libmp3lame) — it is invoked as a subprocess to encode MP3s.
  - macOS: `brew install ffmpeg` · Debian/Ubuntu: `sudo apt-get install -y ffmpeg`
- **espeak-ng does NOT need a separate install**: it is bundled via the `espeakng_loader` dependency
  (installed by `uv sync`) and loaded in-process by the phonemizer. (A system `espeak-ng` on PATH is not
  used and not required.)
- A Telegram bot token from [@BotFather] and an operator-owned archive chat/channel id.

## Install (manual / development)

```bash
uv sync   # provisions the venv; also installs the spaCy en_core_web_sm model (pinned in the lock)
```

## Configure (environment only)

Copy `.env.example` to `.env` and fill it in (or export the variables). Required:
`ANKIVOICE_BOT_TOKEN`, `ANKIVOICE_ARCHIVE_CHAT_ID`. All keys (with defaults):

| Key | Default | Meaning |
|-----|---------|---------|
| `ANKIVOICE_BOT_TOKEN` | — (required) | Bot token (secret). |
| `ANKIVOICE_ARCHIVE_CHAT_ID` | — (required) | Operator archive destination id. |
| `ANKIVOICE_DEFAULT_VOICE` | `af_heart` | American-English voice id. |
| `ANKIVOICE_LANG_CODE` | `a` | `a`=American, `b`=British English. |
| `ANKIVOICE_VOICE_SIDES` | `both` | `both`=voice the Front question and Back answer; `back`=voice the Back answer only (original output). |
| `ANKIVOICE_MAX_CARDS` | `200` | Per-job card cap. |
| `ANKIVOICE_MAX_FILE_BYTES` | `2000000` | Max upload size (bytes). |
| `ANKIVOICE_WORK_DIR` | `./work` | Per-job working dirs (cleaned after delivery). |
| `ANKIVOICE_DB_PATH` | `./data/ankivoice.db` | SQLite job store. |
| `ANKIVOICE_MODEL_DIR` | (HF default cache) | Optional offline model cache (sets `HF_HOME`). |
| `ANKIVOICE_MP3_QUALITY` | `4` | ffmpeg VBR quality. |
| `ANKIVOICE_JOB_HISTORY` | `500` | Max retained terminal job rows (datastore bound). |
| `ANKIVOICE_FFMPEG_TIMEOUT` | `120` | Seconds before an MP3 encode is aborted. |
| `ANKIVOICE_DELIVERY_RETRIES` | `3` | Bounded in-process delivery attempts before deferring to restart. |
| `ANKIVOICE_SKIP_PREFLIGHT` | (unset) | Skip the startup guard (tests/dev only). |
| `ANKIVOICE_ALLOW_DOWNLOADS` | (unset) | Permit model downloads at startup; else the process runs fully offline. |

## One-time offline warm-up

Downloads the Kokoro model weights and the default voice so the bot can run offline afterward. (The
spaCy `en_core_web_sm` G2P model is now installed by `uv sync` — pinned in the lockfile — so the
warm-up no longer needs to fetch it.) The one-command installer above runs this for you; do it
manually only for a dev/manual install:

```bash
uv run python scripts/warmup.py
# then you may run with HF_HUB_OFFLINE=1 for fully offline synthesis
```

## Run

```bash
uv run python -m ankivoice          # long-polling; no public TLS / inbound port needed
```

On startup the service runs a **fail-fast guard**: if `ffmpeg` is missing from PATH, or the phonemizer /
configured voice / model cannot synthesize offline (verified by a one-word out-of-dictionary probe that
also prewarms the model), it exits immediately with a specific message rather than producing
silently-wrong audio or failing on the first job. Run the warm-up once (above) to cache the model/voice.
Set `ANKIVOICE_SKIP_PREFLIGHT=1` to bypass the guard in dev.

## Tests

```bash
uv run pytest          # fast, fully offline default suite (Kokoro + Telegram faked)
uv run pytest -m live  # opt-in: real Kokoro synthesis + real .apkg (self-skips if unavailable)
```

The default suite never touches the network or loads the real model. The single `live` test exercises
the real engines end-to-end and is deselected by default.

## Manual test plan

1. **Start**: set env (`.env`), run `uv run python scripts/warmup.py`, then `uv run python -m ankivoice`.
2. **Send a deck**: message the bot a tab-separated export (see `tests/fixtures/sample_deck.txt`).
3. **Queue reply**: confirm the bot replies with your queue position.
4. **Receive + import**: receive the `.apkg`, import into Anki, reveal each answer → confirm the
   native audio auto-plays and the replay button works; confirm the displayed text is unchanged.
5. **Ordering + overlap**: send two files almost at once → confirm strict one-at-a-time processing in
   arrival order, and that the first deck's delivery overlaps the second's synthesis.
6. **Cleanup + archive**: after delivery, confirm `ANKIVOICE_WORK_DIR` has no `job_*` files and a copy
   of the package is in the archive destination.
7. **Errors**: send a malformed file, an empty file, an oversized file, and one over the card cap →
   confirm each returns a specific, friendly error and the bot stays healthy.

## License & attribution

Uses Kokoro-82M and voices (Apache-2.0), genanki (MIT), python-telegram-bot (LGPL-3.0, used unmodified
as a dependency), ffmpeg (invoked as a separate subprocess), and espeak-ng (GPL-3.0, bundled in-process
as a shared library via the `espeakng_loader` dependency). See
[`research.md`](specs/001-ankivoice-audio-decks/research.md) for the full dependency/license rundown.
