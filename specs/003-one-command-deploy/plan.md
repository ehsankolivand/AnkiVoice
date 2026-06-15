# Implementation Plan: One-Command Install & Deployment

**Branch**: `003-one-command-deploy` | **Date**: 2026-06-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/003-one-command-deploy/spec.md`

## Summary

Make AnkiVoice trivially installable on a fresh Debian/Ubuntu VPS: one command takes a new
operator from a clean host to a running, boot-enabled, auto-restarting bot, after they supply
only a bot token and an archive id. This is **packaging and deployment only** — no
`src/ankivoice/*.py` behavior changes. We add a single idempotent `install.sh` (distro/root
guard → `apt-get install ffmpeg` → install `uv` → dedicated service user + fixed install dir →
copy tree → `uv sync --locked --no-dev` → create/preserve a `0600` `.env` → one-time online warm-up →
install + enable + start a `systemd` unit → verify the app's existing startup preflight is green),
a `deploy/ankivoice.service` unit (venv interpreter directly, `Restart=on-failure`,
`TimeoutStopSec=300` for graceful single-core drain, journald, least-privilege hardening), an
`uninstall.sh` (remove the unit; `--purge` also removes app/data/cache/user, scoped to the app
footprint), one `pyproject.toml` dependency line that **pins `en_core_web_sm` via its wheel URL**
so `uv sync` no longer drops the spaCy model (the cycle-002 defect), and a README Debian deploy
section. The console-script entry point already exists. The app's default test suite stays fast,
offline, and unchanged; the new deploy artifacts get their own self-skipping container/shell test
harness. Everything is built and proven in a throwaway local Debian container — never against the
operator's VPS.

## Technical Context

**Language/Version**: Bash (POSIX-leaning) for `install.sh` / `uninstall.sh`; systemd unit file;
Python 3.12 (`>=3.12,<3.13`) app unchanged; `uv` (Astral) as the provisioner.

**Primary Dependencies**: unchanged app deps (kokoro 0.9.4 / Kokoro-82M CPU, genanki, soundfile,
numpy, python-telegram-bot[ext] 22.x, misaki[en]→spacy 3.8.14). **One added dependency line**:
`en_core_web_sm @ <github wheel URL>` (the spaCy English model the engine already needs at
runtime — pinned so it survives `uv sync`; see research D3). System binary: **ffmpeg** only
(espeak-ng is **bundled** via `espeakng_loader`, never apt-installed). Install-time toolchain:
`uv`, `curl`, `ca-certificates`, `apt`, `systemd`.

**Storage**: unchanged — the single SQLite job store under `<install_dir>/data/`. **No new
datastore, cache, or service** is introduced. The model cache lives under `<install_dir>/models/`
(set via `ANKIVOICE_MODEL_DIR`→`HF_HOME`), populated once by the warm-up and read offline at
runtime.

**Testing**: pytest (app, unchanged: fast + fully offline default suite; one self-skipping `live`
test). New deploy tests under `tests/deploy/` — a shell/container harness that self-skips when
Docker or `systemd-analyze` is unavailable, so the default `uv run pytest` stays green everywhere.

**Target Platform**: Debian 12 (bookworm) / recent Ubuntu LTS, single shared CPU core, ~4 GB RAM,
~40 GB disk, x86_64; outbound internet at install time only. Other distros / non-Linux → clear
refusal. Install logic is architecture-independent (apt + uv fetch per-arch artifacts); the
container proof runs on the dev host's native arch, which exercises 100% of the installer logic.

**Project Type**: single project — long-polling Telegram chat-bot service, plus native deployment
tooling (no app architecture change).

**Performance Goals**: unchanged. Install completes in a few minutes on the target host (dominated
by `uv sync` + the one-time model download). Steady state is the unchanged single-core,
one-synthesis-at-a-time bot.

**Constraints**: 1 core / ~4 GB RAM / flat disk / offline-after-warm-up / env-only config — all
preserved and, for disk scope, **reinforced** (`ReadWritePaths=<install_dir>` confines all writes
to the app's own tree). No inbound port / TLS / reverse proxy (long-polling). Secrets only in a
`0600` service-user-owned `.env`, never committed or logged.

**Scale/Scope**: single host, single operator. Out of scope: multi-host orchestration, web UI,
cloud-provider images, required containerization, inbound networking.

## Constitution Check

*GATE: must pass before Phase 0 and re-checked after Phase 1.* This feature adds **operational
tooling only** and touches no application code path; it preserves every principle and strengthens
the disk-scope one. No violations.

| Principle | How this feature satisfies it | Verdict |
|---|---|---|
| **I. Resource-Bounded** | Runs the unchanged single-core, one-synthesis-at-a-time app. The unit pins it to the host as-is; no new concurrency, no parallelism, no extra long-running process beyond the one bot. `TimeoutStopSec=300` bounds graceful drain. | PASS |
| **II. Agent-Native** | Adds standalone, single-responsibility artifacts (`install.sh`, `uninstall.sh`, one unit file) that don't entangle the app modules; each is independently testable. No module interface changes. | PASS |
| **III. Additive, Non-Breaking** | Zero `src/ankivoice/*.py` behavior change; the end-to-end pipeline and full suite stay green. The only code-adjacent change (the `en_core_web_sm` pin) makes an **already-required** model reproducible — it removes a latent breakage (`uv sync` dropping the model), it does not alter the pipeline. | PASS (stronger) |
| **IV. Local-First, Offline** | The warm-up runs once at install (the only online step); the service then runs with `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` (app default) reading the local `<install_dir>/models` cache. No new outbound destination. | PASS |
| **V. Always Clean Up, Scoped** | The unit's `ProtectSystem=strict` + `ReadWritePaths=<install_dir>` make it **physically impossible** for the bot to write outside its own tree — a defense-in-depth reinforcement of scoped cleanup. `uninstall --purge` is scoped strictly to the app footprint. | PASS (stronger) |
| **VI. Durable, Resumable, Fair** | Graceful `systemd` stop (SIGTERM, verified to reach PTB which awaits the in-flight worker) + `Restart=on-failure` make restarts safe; the app's existing resume logic handles any interrupted job. No queue/fairness change. | PASS |
| **VII. Test-First (NON-NEGOTIABLE)** | Each testable deploy behavior (distro/root refusal, `.env` no-clobber idempotency, `uv sync` keeps `en_core_web_sm`, unit validity via `systemd-analyze verify`, end-to-end active+preflight-green) is written as a failing test first. App's load-bearing tests are untouched and stay green; the live test stays self-skipping. | PASS |
| **VIII. Config/Secrets via Env** | Configuration stays 100% env-only; the installer writes exactly the `ANKIVOICE_*` `.env` the app already reads, `0600` service-user-owned, never logged. Only `.env.example` (no secret) is committed. | PASS (stronger) |

**Resource & Operational Constraints check**: "the only datastore is the SQLite job store — no
additional databases, caches, or services may be introduced for v1." The systemd unit is a
process supervisor, not a datastore/cache/service in the app sense; the `<install_dir>/models`
HuggingFace cache is the **same** model cache the app already uses at runtime (we only relocate it
to a stable path the service user can read) — not a new datastore. **No violation.**

**`en_core_web_sm` dependency — GATE NOTE (not a violation).** Principle III's "additive,
non-breaking" and the 002 intent of "no new runtime dependency" are about not adding new
*capabilities/datastores*. `en_core_web_sm` is **already a hard runtime requirement** of the
speech engine (misaki's English G2P loads it; the warm-up has always fetched it). It is absent
from `uv.lock` only because it ships as a separate model wheel, so a plain `uv sync` prunes it —
the documented cycle-002 drop. Pinning it via its wheel URL adds **no new capability**; it makes
an **existing, already-required** artifact reproducible and fixes a real defect. Recorded in
research D3 and Complexity Tracking.

**Post-Phase-1 re-check**: design artifacts add no new datastore/cache/service, no extra
concurrency, no app behavior change, no out-of-scope feature; the single dependency line is a
reproducibility fix for an existing requirement → still **PASS**.

## Project Structure

### Documentation (this feature)

```text
specs/003-one-command-deploy/
├── plan.md              # This file (/speckit-plan output)
├── spec.md              # Feature requirements (FR-001..FR-018, SC-001..SC-007)
├── research.md          # Phase 0 — verified deploy decisions (D1..D8)
├── data-model.md        # Phase 1 — deployment entities + the install state model
├── quickstart.md        # Phase 1 — how to validate the install end-to-end (container)
├── contracts/
│   └── deploy-interface.md   # The CLI/unit/env contracts for install.sh, uninstall.sh, the unit
├── checklists/
│   └── requirements.md  # Spec quality checklist (passed)
└── tasks.md             # Phase 2 — /speckit-tasks output (test-first; NOT created here)
```

### Source Code (repository root) — additions/changes only

```text
install.sh                  # NEW: one-command idempotent installer (root, Debian/Ubuntu)
uninstall.sh                # NEW: remove the service; --purge removes app/data/cache/user (scoped)
deploy/
└── ankivoice.service       # NEW: systemd unit template ({{USER}}/{{INSTALL_DIR}} placeholders)
pyproject.toml              # CHANGE: add the en_core_web_sm wheel-URL dependency (one line)
uv.lock                     # CHANGE: re-locked to include en_core_web_sm (pinned URL + sha256)
.env.example                # CHANGE: document ANKIVOICE_MODEL_DIR usage for the service cache
README.md                   # CHANGE: add the Debian one-command deploy section; reconcile install wording
tests/deploy/
├── __init__.py             # NEW
├── conftest.py             # NEW: docker/systemd availability detection + self-skip markers
├── test_install_unit.py    # NEW: pure-shell unit tests (distro/root refusal, .env render, arg parse) in a sandbox
├── test_systemd_unit.py    # NEW: render the unit + `systemd-analyze verify` (skips if unavailable)
├── test_uv_sync_keeps_model.py # NEW: assert en_core_web_sm is in uv.lock and survives a sync (skips if no net/uv)
└── test_install_container.py   # NEW: full end-to-end in a throwaway Debian container (skips if no docker)

# NO changes to src/ankivoice/*.py and NO changes to the app's existing tests.
scripts/warmup.py           # UNCHANGED behavior (its spaCy-download branch becomes a no-op now that
                            #   en_core_web_sm is installed by uv sync; it still fetches Kokoro weights+voice)
```

**Structure Decision**: Single project + native deployment tooling at the repo root. Deploy
scripts live at the top level (operator-facing: `sudo ./install.sh`), the unit template under
`deploy/`, and deploy tests under `tests/deploy/` (isolated and self-skipping so the default suite
is unaffected). No application source or test is modified.

## Key Design Decisions (detail in research.md / contracts)

1. **Fixed install dir via tree-copy (idempotent update path).** `install.sh` copies the project
   into `INSTALL_DIR` (default `/opt/ankivoice`) with a portable `tar`-pipe that **excludes**
   `.git .venv work data models .env __pycache__ *.pyc`. Re-running = update (refresh code + deps);
   the exclude list guarantees the operator's `.env`, data, and model cache are never clobbered.
   Overridable: `ANKIVOICE_USER`, `INSTALL_DIR`/`ANKIVOICE_PREFIX`.

2. **Service user owns everything; runtime uses the venv python directly.** A dedicated
   `--system` user (default `ankivoice`, nologin, home = install dir) owns the tree, `.venv`,
   `.env` (`0600`), data, and model cache. `uv sync --locked --no-dev` runs as that user.
   `ExecStart=<install_dir>/.venv/bin/python -m ankivoice` — **no `uv` on the runtime PATH**.

3. **`.env` is create-or-preserve, never clobber.** Two required keys come from CLI flags / env /
   silent prompt; the installer also writes `ANKIVOICE_MODEL_DIR=<install_dir>/models`. An existing
   `.env` is left byte-for-byte unchanged (no prompt, no overwrite). `0600`, service-user-owned,
   never echoed/logged.

4. **Warm-up reused as-is**, run once online as the service user with `ANKIVOICE_ALLOW_DOWNLOADS=1`
   and `HF_HOME=<install_dir>/models`. Downloads Kokoro weights + voice; `en_core_web_sm` is
   already present from `uv sync`. The service then runs offline (app default sets
   `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`).

5. **Preflight reused for the success gate.** The app's existing startup guard runs the probe
   synthesis before `run_polling`; the installer confirms success by watching the journal for the
   preflight pass + long-polling start line, and gives a specific hint on a Telegram 401 (a
   bad/placeholder token is the operator's data problem, not an install failure).

6. **Unit hardening reinforces P5.** `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`,
   `PrivateTmp`, `ReadWritePaths=<install_dir>` — the bot can only write inside its own tree.
   `TimeoutStopSec=300` so a ~2-minute single-core job finishes/parks before SIGKILL. Validated by
   `systemd-analyze verify`.

7. **`en_core_web_sm` pin** (the cycle-002 fix): one PEP 508 `name @ url` line in
   `[project.dependencies]`; `uv lock` records the pinned URL + sha256; `uv sync` keeps it.

## Complexity Tracking

| Item | Why it exists | Why the simpler alternative was rejected |
|---|---|---|
| Added dependency line `en_core_web_sm @ <wheel-url>` | The spaCy English G2P model is an **existing** hard runtime requirement that `uv sync` silently prunes (cycle-002 drop), breaking a fresh install. | "Re-download it in the warm-up each install" (the status quo) is non-reproducible and was the source of the drop; "`spacy download` in the unit/ExecStartPre" adds a runtime online step that violates offline-after-warm-up. Pinning the wheel is the only reproducible, offline-preserving fix. |
| Tree-copy into a fixed install dir | Gives a clean, supervised, fixed-path service install independent of where the operator cloned, and a safe idempotent update path. | "Run in place from the clone" couples the service to an arbitrary user dir and makes ownership/permissions and updates fragile. |
| systemd hardening directives | Least privilege; `ReadWritePaths` reinforces the constitution's scoped-write guarantee. | A bare unit works but forgoes a free, verified-safe defense-in-depth that directly serves Principle V. |
