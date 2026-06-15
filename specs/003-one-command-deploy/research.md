# Research & Decisions: AnkiVoice cycle 003 (one-command install & deployment)

Every decision below was verified this session against the official source or empirically (uv
0.5.21 on this host), per the constitution's verify-don't-guess rule — never from memory. Format:
Decision / Rationale / Alternatives rejected. These pin the implementation. No open
NEEDS-CLARIFICATION items remain.

## D1 — Distro & privilege guard (FR-012, SC-007)

**Decision**: `install.sh` first refuses, making **no changes**, when (a) not run as root
(`[ "$(id -u)" -ne 0 ]`), or (b) the host is not Debian/Ubuntu. Detection sources
`/etc/os-release` in a subshell (so its vars don't leak) and accepts iff `ID` ∈ {`debian`,
`ubuntu`} **or** any token of `ID_LIKE` equals `debian`; otherwise it prints a specific message
naming the supported targets and exits non-zero. Guard against a missing/unreadable
`/etc/os-release`.

**Rationale**: `/etc/os-release` is the freedesktop-standard, script-stable identifier (`ID` is
lower-case, version-free, "suitable for processing by scripts"; the spec explicitly tells build
scripts to fall back to `ID_LIKE`). Refusing before any apt/user/file mutation guarantees "changes
nothing" on an unsupported host.

**Alternatives rejected**: `lsb_release` (not always installed); `uname` (says nothing about the
package manager); parsing `/etc/debian_version` (present on derivatives but misses the
distro identity / refusal message clarity).

## D2 — System packages: ffmpeg only; espeak-ng stays bundled (read-first invariant)

**Decision**: `DEBIAN_FRONTEND=noninteractive apt-get update` then
`apt-get install -y --no-install-recommends ffmpeg curl ca-certificates`. **Never** apt-install
`espeak-ng`.

**Rationale**: The preflight/README/code (cycle-002, verified) establish that misaki loads
espeak-ng from a **bundled** shared library via the `espeakng_loader` Python dependency
(`EspeakWrapper.set_library(...)`), not from a PATH binary — synthesis of out-of-dictionary words
works with no `espeak-ng` on PATH. ffmpeg (with libmp3lame, the default in Debian's `ffmpeg`
package) is the only required system binary; it is invoked as a subprocess to encode MP3s. `curl`
+ `ca-certificates` are needed only to fetch the uv installer.

**Alternatives rejected**: also installing `espeak-ng` (a false dependency that would mask the
bundled-loader design and could shadow it); `apt-get install ffmpeg` without
`--no-install-recommends` (pulls unneeded recommends on a small host).

## D3 — Pin `en_core_web_sm` via its wheel URL so `uv sync` keeps it (the cycle-002 drop)  ★

**Decision**: add exactly one line to `[project.dependencies]` in `pyproject.toml`:

```toml
"en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl",
```

then `uv lock` (commit `uv.lock`). No `[tool.uv.sources]` entry, no version specifier alongside
the URL, no other dependency change.

**Root cause (investigated & confirmed)**: `kokoro 0.9.4` → `misaki[en]` → `spacy 3.8.14`, but the
spaCy *model* `en_core_web_sm` ships as a **separate GitHub-release wheel**, not a PyPI package, so
it was never in `uv.lock`. `uv sync` is exact and **prunes anything not in the lockfile** — so a
plain `uv sync` deletes a previously `spacy download`-ed `en_core_web_sm` from the venv. That is
the documented cycle-002 drop. The model is a hard runtime requirement: misaki's English G2P loads
it, and the preflight probe (`"Warmup Zbigniew."`) exercises exactly that path.

**Verification (empirical, this session)**:
- Wheel URL is live: HTTP 200 (302→release-assets CDN), `content-length` 12 806 118 bytes (~12.2 MB),
  sha256 `1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85` — confirmed by two
  independent agents.
- In a throwaway project, `uv lock` (uv 0.5.21) with the line above + the existing resolution
  resolved with **no conflict** and wrote a lock entry `name = "en-core-web-sm" / version = "3.8.0"
  / source = { url = … }` with the wheel's sha256 → it is pinned and **survives `uv sync`**.
- The model wheel has **zero `Requires-Dist`** (spaCy models don't declare `spacy` as a pip dep),
  so it will neither pull in nor fight the locked `spacy 3.8.14`. spaCy enforces the
  `>=3.8.0,<3.9.0` model-compat range at **load time**; `3.8.14` is inside it → compatible.

**Rationale**: This is the only fix that is both **reproducible** (locked URL + hash) and
**offline-preserving** (the model is present after `uv sync`, before any warm-up, so nothing must
be fetched at runtime). Keeping `spacy` pinned via its existing transitive lock + the matching
model wheel guarantees compatibility.

**Alternatives rejected**: keep relying on `python -m spacy download` in the warm-up (non-reproducible;
the exact thing that dropped); add `spacy download` to the unit / `ExecStartPre` (a runtime online
step → violates offline-after-warm-up, Principle IV); vendor the wheel into the repo (bloats VCS,
duplicates what the lock+hash already pin). NB: a direct-URL dep makes the project non-publishable
to PyPI — irrelevant here (we only `uv sync`; AnkiVoice is not published to PyPI).

## D4 — Install uv to a system path; reproducible locked sync; run the venv python directly

**Decision**:
- Install uv only if absent: `curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh`.
  `/usr/local/bin` is already on every user's PATH, so the service user can run `uv` at install
  time; `UV_NO_MODIFY_PATH=1` keeps the installer from editing shell profiles.
- Provision as the service user, in the install dir: **`uv sync --locked --no-dev`**.
- Runtime never uses uv: `ExecStart=<install_dir>/.venv/bin/python -m ankivoice`.

**Rationale (official Astral docs)**: the standalone installer is non-interactive by design;
`UV_INSTALL_DIR` controls only the binary location. `uv sync` is exact (prunes non-locked
packages); `--locked` **errors loudly if `uv.lock` is stale** (the correct, reproducible choice —
it fails on drift instead of silently re-resolving), and `--no-dev` excludes the dev group (pytest
etc.) from the production venv. `uv sync` creates `.venv`; invoking `.venv/bin/python` directly
removes any runtime dependency on uv being on PATH.

**Alternatives rejected**: `--frozen` (skips the up-to-date check — more permissive; only needed
as a can't-validate-yet fallback, which we don't have); installing uv into `~/.local/bin`
(ambiguous `$HOME` under sudo/service-user); `uv run -m ankivoice` at runtime (needs uv on PATH and
re-checks the env on every start — unnecessary coupling for a service).

## D5 — systemd unit: simple, hardened, graceful (FR-008, FR-011)

**Decision** (`deploy/ankivoice.service`, rendered with the resolved user + install dir):

```ini
[Unit]
Description=AnkiVoice — Telegram audio-deck bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={{USER}}
Group={{USER}}
WorkingDirectory={{INSTALL_DIR}}
EnvironmentFile={{INSTALL_DIR}}/.env
ExecStart={{INSTALL_DIR}}/.venv/bin/python -m ankivoice
Restart=on-failure
RestartSec=5
TimeoutStopSec=300
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths={{INSTALL_DIR}}

[Install]
WantedBy=multi-user.target
```

**Rationale (official systemd man pages + PTB source)**:
- `Type=simple` is correct for a plain blocking `run_polling` loop; default `KillMode=control-group`
  delivers SIGTERM to the python main process.
- **`TimeoutStopSec=300`**: the distro default (`DefaultTimeoutStopSec`, ~90 s) is too short for a
  ~2-minute single-core synthesis job; after the timeout systemd sends SIGKILL. 300 s gives the
  in-flight job room to finish/park (D6). Not `infinity` (a hung process must not block shutdown
  forever).
- **No `KillSignal` override**: systemd's default is already SIGTERM, which matches PTB's default
  `stop_signals` — they line up out of the box.
- Hardening: `ProtectSystem=strict` makes the filesystem read-only except the listed
  `ReadWritePaths={{INSTALL_DIR}}` — so the bot can **only** write inside its own tree (work/, data/,
  models/), a defense-in-depth reinforcement of Principle V. `NoNewPrivileges`, `ProtectHome`,
  `PrivateTmp` are free, safe least-privilege wins (genanki temp is already scoped into the job dir,
  ffmpeg writes to explicit out-paths, so `PrivateTmp` is safe).
- `EnvironmentFile={{INSTALL_DIR}}/.env` (no leading `-`): a missing env is a loud failure, not a
  silent half-start. systemd reads it as root before dropping to `User=`, so `0600`
  service-user-owned is fine (root reads anything). The `.env` is simple `KEY=value` (no spaces in
  token/id) → parses identically under systemd and python-dotenv.

**Validation**: `systemd-analyze verify /etc/systemd/system/ankivoice.service` (run in the container
**after** install, when the ExecStart path + user exist, to avoid its known executable/user
existence warnings). Use `--recursive-errors=yes` for stricter dependency checks.

**Alternatives rejected**: `Type=notify` (the app doesn't call `sd_notify`); `EnvironmentFile` under
`/etc/ankivoice/` (a second location to manage — keeping `.env` in the install dir keeps the whole
footprint in one tree, simplifying `ReadWritePaths` and uninstall); a shell-wrapper ExecStart (risks
the signal hitting the shell instead of python).

## D6 — Graceful stop is already correct in the app (no app change)

**Decision**: rely on the existing behavior; add no signal handling to the app.

**Rationale (verified against PTB v22 source)**: `run_polling` installs default `stop_signals =
(SIGINT, SIGTERM, SIGABRT)` on Unix; SIGTERM raises `SystemExit` in the loop → the `finally` block
runs `updater.stop()` → `Application.stop()` → `post_stop` → `Application.shutdown()` →
`post_shutdown`. `Application.stop()` awaits tasks started via `Application.create_task` — and the
worker is started exactly that way in `bot._post_init` (`app.create_task(worker.run(stop), …)`), so
PTB awaits it; `_post_shutdown` additionally sets the stop event and awaits the worker task. The
worker drains in-flight deliveries on shutdown and the SQLite store resumes any interrupted job on
the next start. So `systemctl stop`/restart/uninstall give a clean, lossless stop within
`TimeoutStopSec`.

**Alternatives rejected**: adding a custom signal handler or `ExecStop` (redundant and risks
fighting PTB's own handlers).

## D7 — Offline model cache pinned to the install dir (FR-006, FR-017, SC-004)

**Decision**: set `ANKIVOICE_MODEL_DIR=<install_dir>/models` in `.env`. The app maps this to
`HF_HOME` (before importing torch/kokoro) and, unless `ANKIVOICE_ALLOW_DOWNLOADS` is set, defaults
`HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1`. The warm-up runs once with
`ANKIVOICE_ALLOW_DOWNLOADS=1` + `HF_HOME=<install_dir>/models` to populate
`<install_dir>/models/hub/models--hexgrad--Kokoro-82M/...`; the service then reads that cache
offline.

**Rationale (official HF Hub docs)**: `HF_HOME` relocates the whole cache; the hub cache lands at
`$HF_HOME/hub`. These vars are read at import time, which the app already respects
(`speech._load_pipeline` sets `HF_HOME` before importing torch/kokoro; `__main__` sets the offline
flags). Putting the cache under the install dir makes it service-user-owned, readable offline, and
covered by `ReadWritePaths` (warm-up writes there; runtime only reads).

**Warm-up/runtime cache MUST agree (spec clarification Q2).** Because `.env` is preserved
byte-for-byte (D8), an older `.env` may lack `ANKIVOICE_MODEL_DIR`. The installer therefore derives
the **effective** cache the running service will use and warms up *that same* location, rather than
forcing `<install_dir>/models`:
- Fresh `.env` (installer-created): `ANKIVOICE_MODEL_DIR=<install_dir>/models` → warm up there.
- Existing `.env` with `ANKIVOICE_MODEL_DIR=X` → warm up `HF_HOME=X`.
- Existing `.env` without `ANKIVOICE_MODEL_DIR` → the service (systemd `User=`) gets `HOME` =
  the service user's home = the install dir, so the default HF cache is `<install_dir>/.cache/huggingface`;
  warm up with that same `HF_HOME` (still under the install dir → covered by `ReadWritePaths`).
The installer reads the effective `ANKIVOICE_MODEL_DIR` by sourcing the keys it needs from the
preserved `.env` (it never writes to it).

**Alternatives rejected**: leaving the cache at the service user's `~/.cache/huggingface` (works,
but scatters the footprint and complicates `ProtectHome`/uninstall); a system-wide `/var/cache`
(extra path to manage and to add to `ReadWritePaths`).

## D8 — `.env` create-or-preserve; secrets hygiene; idempotent update via tree-copy (FR-007, FR-010, FR-015, SC-002)

**Decision**:
- Install copies the project into the fixed `INSTALL_DIR` with a **portable `tar`-pipe** that
  excludes `.git .venv work data models .env __pycache__ *.pyc`. Re-running refreshes code + deps
  (the update path) and **cannot** touch the operator's `.env`/data/cache.
- `.env`: if `<install_dir>/.env` exists, it is left **byte-for-byte unchanged** (no prompt, no
  write). Otherwise it is created from the two required values (CLI flag `--token`/`--archive-id`,
  or env vars, or interactive prompt with the token read silently via `read -rs`), plus
  `ANKIVOICE_MODEL_DIR=<install_dir>/models`, then `chmod 600` + `chown {{USER}}`. The secret is
  never echoed or logged (no `set -x` around it; prompts go to stderr; the value never appears in
  any printed command).

**Rationale**: a `tar`-pipe needs no rsync/git and respects an explicit exclude list, so the
idempotency and no-clobber guarantees are structural, not best-effort. `0600` + service-user
ownership is least privilege for a secret. Committing only `.env.example` keeps secrets out of VCS
(Principle VIII).

**Alternatives rejected**: `rsync --exclude` (not installed by default on minimal Debian);
`cp -a` of the whole tree (copies `.git`/`.venv`/secrets); `git archive` (assumes a git checkout —
the operator may have a tarball); writing `.env` then "merging" on re-run (risks clobbering an
operator edit — preserve-as-is is safer and is what SC-002 demands).

## D9 — Container proof strategy (test-first, throwaway only)

**Decision**: prove the installer end-to-end in a **throwaway** `debian:12` container run with
systemd as PID 1 (`--privileged`, cgroup mount, `/sbin/init`), on the dev host's **native arch**
(fast, exercises 100% of the installer logic, which is arch-independent). The deploy test harness
(`tests/deploy/`) self-skips when Docker / `systemd-analyze` is unavailable so the default
`uv run pytest` is unaffected on any host. With a **placeholder** token the success boundary is
"unit started + the app's startup preflight passed + the long-polling start line is in the journal"
(a real token keeps it continuously active); idempotency and `--purge` cleanliness are asserted in
the same container. **Never run against the operator's VPS.**

**Rationale**: the prompt sanctions exactly this boundary (a placeholder token can't complete a real
Telegram login). Native-arch keeps each heavy run (torch + the one-time model download) tractable
while still validating the real apt→uv→sync→warm-up→unit→preflight path.

**Alternatives rejected**: emulated `linux/amd64` under qemu (the warm-up synthesis becomes
prohibitively slow); a mock systemd (wouldn't prove `enable --now`/journald/graceful stop); testing
on a real VPS (explicitly forbidden).

## D10 — Uninstall: remove the unit by default; `--purge` is scoped and explicit (FR-013, SC-005)

**Decision**: `uninstall.sh` stops + disables + removes the unit and `daemon-reload`s by default,
leaving the install dir/data/`.env` in place. `--purge` (with a typed confirmation unless `--yes`)
additionally removes `INSTALL_DIR`, the data + model cache (under it), and the service user
(`userdel`), each guarded so it only ever removes paths within the app footprint (refuse to
`rm -rf` a non-AnkiVoice or root path; only delete the resolved `INSTALL_DIR`).

**Rationale**: reversibility builds operator trust; making destructive removal explicit + scoped
honors Principle V (only ever delete what we created).

**Alternatives rejected**: purging by default (foot-gun — could delete data on a routine
uninstall); deleting the apt-installed ffmpeg/uv (system-shared; not part of the app footprint).
