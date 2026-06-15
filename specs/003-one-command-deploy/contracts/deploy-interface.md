# Deploy Interface Contract: cycle 003

The exact operator-facing contracts for the new deployment artifacts. These are the surfaces the
tasks/tests assert against. Nothing here changes any `src/ankivoice/*.py` module interface (the 001
[module-interfaces.md](../../001-ankivoice-audio-decks/contracts/module-interfaces.md) and 002
[changes.md](../../002-quality-bugfix-perf/contracts/changes.md) remain accurate).

## `install.sh`

```text
Usage: sudo ./install.sh [options]

Options (all optional; sane defaults):
  --token <BOT_TOKEN>          ANKIVOICE_BOT_TOKEN (else $ANKIVOICE_BOT_TOKEN, else prompt)
  --archive-id <CHAT_ID>       ANKIVOICE_ARCHIVE_CHAT_ID (else $ANKIVOICE_ARCHIVE_CHAT_ID, else prompt)
  --user <NAME>                service user (else $ANKIVOICE_USER, else "ankivoice")
  --prefix <DIR>               install dir (else $INSTALL_DIR/$ANKIVOICE_PREFIX, else "/opt/ankivoice")
  --non-interactive            never prompt; fail if a required value is missing and no .env exists
  --skip-warmup                skip the one-time model download (for tests/CI; service then needs a later warm-up)
  -h, --help                   usage

Env overrides: ANKIVOICE_USER, INSTALL_DIR / ANKIVOICE_PREFIX, ANKIVOICE_BOT_TOKEN,
               ANKIVOICE_ARCHIVE_CHAT_ID.
```

**Behavioral contract (MUST):**

1. Exit non-zero with a specific message and **make no host changes** when not root, or when
   `/etc/os-release` does not identify Debian/Ubuntu (via `ID` ∈ {debian,ubuntu} or `ID_LIKE`
   token `debian`). *(FR-012, SC-007)*
2. Be idempotent: a second run completes successfully, refreshes code + deps, and leaves the
   service active/enabled. *(FR-010, SC-002)*
3. **Never** overwrite, prompt for, or print an existing `<install_dir>/.env`; preserve it
   byte-for-byte with unchanged `0600` + ownership. *(FR-007, FR-010, SC-002)*
4. Write a new `.env` only when absent: `0600`, owned by the service user, containing the two
   required keys + `ANKIVOICE_MODEL_DIR=<install_dir>/models`. The token is never echoed to the
   terminal or any log. *(FR-007, FR-015)*
5. apt-install **ffmpeg** (+ curl, ca-certificates); **never** apt-install espeak-ng. *(FR-002, D2)*
6. Provision via `uv sync --locked --no-dev` as the service user, yielding
   `<install_dir>/.venv`. *(FR-003, FR-005)*
7. Run the one-time warm-up online (unless `--skip-warmup`) into the **effective** cache the
   service will read — fresh `.env` → `<install_dir>/models`; preserved `.env` → its
   `ANKIVOICE_MODEL_DIR` if set, else the service user's default HF cache (under the install dir) —
   so the service runs offline after. The installer never injects keys into a preserved `.env`.
   *(FR-006, SC-004, clarification Q2)*
8. Install + `daemon-reload` + `enable --now` the unit, then confirm via the journal that the
   app's startup preflight passed and long-polling started; a Telegram 401 yields a specific
   "token rejected" hint but is **not** an install failure; a preflight/unit failure exits
   non-zero with the offending journal line. *(FR-008, FR-009, SC-001)*
9. On success, print copy-paste hints: view logs, status, restart, update (re-run), uninstall.
   *(FR-014, SC-003)*
10. Exit codes: `0` success (incl. the token-rejected-but-installed case, which prints a clear
    warning); non-zero on guard refusal, apt/uv/sync/warm-up failure, or preflight/unit failure.

## `uninstall.sh`

```text
Usage: sudo ./uninstall.sh [--purge] [--yes] [--user <NAME>] [--prefix <DIR>]

  (default)        stop + disable + remove the unit, daemon-reload; leave app/data/.env intact
  --purge          ALSO remove the install dir, data, model cache, and the service user
  --yes            skip the interactive confirmation that --purge otherwise requires
  --user/--prefix  resolve the same user/dir the install used (else defaults/env)
```

**Behavioral contract (MUST):**

1. Default mode removes only the unit (stop, disable, `rm` unit file, `daemon-reload`); it leaves
   `<install_dir>`, data, and `.env` on disk. *(FR-013, SC-005)*
2. `--purge` additionally removes the resolved `<install_dir>` and the service user, after a typed
   confirmation unless `--yes`. *(FR-013, SC-005)*
3. Scope guard: refuse to delete a path that is not the resolved install dir (e.g. `/`, `/opt`,
   `$HOME`); only ever `rm -rf` the exact `<install_dir>`. Never removes the apt-installed ffmpeg
   or uv (host-shared, not app footprint). *(FR-013, Constitution P5)*
4. Be safe to re-run (a second uninstall is a no-op success).

## `deploy/ankivoice.service` (template → `/etc/systemd/system/ankivoice.service`)

Placeholders `{{USER}}` and `{{INSTALL_DIR}}` are substituted by `install.sh`. The rendered unit
MUST pass `systemd-analyze verify` (run after install, when the ExecStart path + user exist). Key
directives are fixed by [research D5](../research.md):

- `Type=simple`, `User`/`Group`=`{{USER}}`, `WorkingDirectory={{INSTALL_DIR}}`,
  `EnvironmentFile={{INSTALL_DIR}}/.env`, `ExecStart={{INSTALL_DIR}}/.venv/bin/python -m ankivoice`.
- `Restart=on-failure`, `RestartSec=5`, **`TimeoutStopSec=300`**, default `KillSignal=SIGTERM`.
- `NoNewPrivileges=true`, `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`,
  `ReadWritePaths={{INSTALL_DIR}}`.
- `[Install] WantedBy=multi-user.target`.

**Behavioral contract (MUST):** journald-logged; auto-restart on failure; on `systemctl stop` the
app receives SIGTERM, drains the in-flight job within `TimeoutStopSec`, and exits cleanly
(graceful — D6); enabled to start on boot.

## `pyproject.toml` change (contract)

`[project.dependencies]` gains exactly one line — the `en_core_web_sm` 3.8.0 direct wheel URL
([research D3](../research.md)) — and `uv.lock` is regenerated to include it (pinned URL + sha256).
The existing `[project.scripts] ankivoice = "ankivoice.__main__:main"` entry point is unchanged. No
other dependency, no `[tool.uv.sources]`, no version specifier alongside the URL.

**Invariant tested:** after `uv sync` (any mode) in a clean environment, `en_core_web_sm` is
importable / present in the environment — i.e. the cycle-002 drop cannot recur. *(FR-005, FR-018)*

## Unchanged app contract (explicit)

No change to: the bot interface, the module interfaces, `config.load_config` keys/semantics, the
preflight probe, the worker/queue/state machine, or the synthesized output. The app's default test
suite (fast, offline) and the single self-skipping `live` test are unmodified. *(FR-016, FR-018,
SC-006)*
