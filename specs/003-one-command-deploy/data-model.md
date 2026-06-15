# Data Model: cycle 003 (one-command install & deployment)

This feature introduces **no application data entities** and **no datastore changes** — the SQLite
job store and the `Job`/`JobState` model are untouched. The "entities" here are deployment-time
host artifacts the installer creates and the uninstaller removes. They are filesystem/OS objects,
not rows.

## Deployment entities

| Entity | Concrete form | Owner / perms | Created by | Removed by |
|---|---|---|---|---|
| **Service user** | `--system` user, default `ankivoice` (override `ANKIVOICE_USER`), nologin shell, home = install dir | itself | `install.sh` (`useradd --system`) if missing | `uninstall.sh --purge` (`userdel`) |
| **Install dir** | fixed path, default `/opt/ankivoice` (override `INSTALL_DIR`/`ANKIVOICE_PREFIX`) | service user | `install.sh` | `uninstall.sh --purge` |
| **App tree** | project files under install dir (code, `pyproject.toml`, `uv.lock`, `scripts/`) | service user | `install.sh` tar-pipe copy (excludes `.git .venv work data models .env __pycache__ *.pyc`) | with install dir on `--purge` |
| **Virtualenv** | `<install_dir>/.venv` | service user | `uv sync --locked --no-dev` | with install dir on `--purge` |
| **Operator config** | `<install_dir>/.env`, `0600` | service user | `install.sh` (create-or-preserve) | with install dir on `--purge` (never on a default uninstall) |
| **Data dir** | `<install_dir>/data/` (SQLite job store) | service user | app/installer | with install dir on `--purge` |
| **Work dir** | `<install_dir>/work/` (per-job scratch, cleaned by app) | service user | app/installer | with install dir on `--purge` |
| **Model cache** | `<install_dir>/models/` (`HF_HOME`; `hub/models--hexgrad--Kokoro-82M/...`) | service user | warm-up (one-time, online) | with install dir on `--purge` |
| **systemd unit** | `/etc/systemd/system/ankivoice.service` | root | `install.sh` (rendered from `deploy/ankivoice.service`) | `uninstall.sh` (default **and** purge) |

## Operator configuration keys written by the installer

Only the keys the installer is responsible for; every other `ANKIVOICE_*` key keeps its app default
(see `.env.example`). The installer **never** overwrites an existing `.env`.

| Key | Source | Notes |
|---|---|---|
| `ANKIVOICE_BOT_TOKEN` | `--token` flag, env var, or silent prompt | secret; `0600`; never echoed/logged |
| `ANKIVOICE_ARCHIVE_CHAT_ID` | `--archive-id` flag, env var, or prompt | integer chat/channel id |
| `ANKIVOICE_MODEL_DIR` | computed = `<install_dir>/models` | pins `HF_HOME` to a stable, service-user-readable, offline cache |

## Install state model (idempotent; re-runnable)

The installer is a forward-only, idempotent sequence. Each step is a no-op (or a safe refresh) when
already satisfied, so a re-run is the supported **update** path. Any step's failure aborts before
the service is (re)started, surfacing a specific message; an interrupted run is safe to re-run.

```text
[guard]      not-root OR unsupported-distro ──► REFUSE (no changes)   (FR-012, SC-007)
   │ ok
[apt]        ensure ffmpeg, curl, ca-certificates                     (idempotent; apt no-ops if present)
   │
[uv]         ensure uv on /usr/local/bin                              (skip if `uv` present)
   │
[user+dir]   ensure service user + install dir                       (create if missing)
   │
[copy]       tar-pipe app tree → install dir (excl. secrets/data)    (refresh on re-run; never .env)
   │
[sync]       uv sync --locked --no-dev   (as service user)           (en_core_web_sm retained → D3)
   │
[config]     .env exists ? PRESERVE : create 0600 from inputs        (no clobber; FR-007/FR-010/SC-002)
   │
[warmup]     one-time online warm-up into the EFFECTIVE cache         (idempotent; re-validates cache)
   │            (fresh .env → <install_dir>/models ; preserved .env → its ANKIVOICE_MODEL_DIR,
   │             else the service user's default HF cache under the install dir — Q2)
   │
[unit]       render + install unit, daemon-reload, enable --now      (FR-008)
   │
[verify]     journal shows preflight-green + long-polling start ?    (FR-009)
   │            ├─ yes ──► ACTIVE/ENABLED ──► print next-step hints   (SC-001)
   │            ├─ Telegram 401 (bad/placeholder token) ──► WARN "token rejected", install still ok
   │            └─ preflight/unit failure ──► FAIL loudly with the specific journal line
```

## Uninstall state model

```text
[default]  systemctl stop+disable ankivoice ; rm unit ; daemon-reload
           └─► install dir / data / .env left intact
[--purge]  default steps, then (after typed confirm unless --yes):
           rm -rf <resolved INSTALL_DIR>  (guarded: refuse if not the resolved app dir)
           userdel <service user>
           └─► no service, no app/data/model-cache/user residue        (SC-005)
```

No state is persisted by the scripts themselves beyond these host artifacts; "state" is read live
from the host (`id -u`, `/etc/os-release`, `command -v uv`, `getent passwd`, `test -f .env`,
`systemctl is-active/is-enabled`, the journal).
