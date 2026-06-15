# Tasks: One-Command Install & Deployment

**Feature**: `003-one-command-deploy` | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

**Inputs**: [research.md](./research.md) (D1–D10), [contracts/deploy-interface.md](./contracts/deploy-interface.md),
[data-model.md](./data-model.md), [quickstart.md](./quickstart.md).

**Discipline**: Strict TDD (Constitution VII) for every testable deploy behavior — failing test
first, watch it fail for the right reason, then the minimal implementation. The app's default
pytest suite stays fast/offline and **unchanged** (no `src/ankivoice/*.py` or existing-test edits).
All deploy tests live under `tests/deploy/` and **self-skip** when docker / `systemd-analyze` /
network / `uv` are unavailable, so `uv run pytest` stays green on any host. Build & prove ONLY in a
throwaway local Debian container — never the operator's VPS.

**Path conventions**: repo root = `/Users/ehsankolivans/Desktop/projects/AnkiVoice/AnkiVoice`.
New: `install.sh`, `uninstall.sh`, `deploy/ankivoice.service`, `tests/deploy/*`. Changed:
`pyproject.toml`, `uv.lock`, `.env.example`, `README.md`.

---

## Phase 1: Setup

- [x] T001 [P] Create the deploy-test package skeleton: `tests/deploy/__init__.py` and
  `tests/deploy/conftest.py` providing reusable pytest skip helpers/fixtures —
  `have_docker()`, `have_systemd_analyze()`, `have_uv()`, `network_available()`, a `repo_root`
  fixture, and a `run_install(args, env, cwd)` sandbox helper — so every deploy test can self-skip
  cleanly. (Plan: tests/deploy; Constitution VII fast-offline-default.)
- [x] T002 [P] Add the `en_core_web_sm` wheel-URL line to `[project.dependencies]` in
  `pyproject.toml` exactly per [research D3](./research.md) (PEP 508 `name @ url`, no
  `[tool.uv.sources]`, no version specifier). Keep the existing `[project.scripts]` entry point.
  (FR-005; contract "pyproject.toml change".)

---

## Phase 2: Foundational (blocking prerequisites)

These unblock the container e2e and the model-retention guarantee.

- [x] T003 Run `uv lock` to regenerate `uv.lock` with the `en-core-web-sm` 3.8.0 entry (pinned URL
  + sha256); confirm `uv sync` then `uv run python -c "import en_core_web_sm"` succeeds locally.
  Commit `uv.lock`. (FR-005; depends on T002.)
- [x] T004 [P] Write FAILING test `tests/deploy/test_uv_sync_keeps_model.py`: (a) always-on —
  assert `uv.lock` contains an `en-core-web-sm` package entry with a wheel URL + a sha256 hash;
  (b) gated on `have_uv()`+`network_available()` — `uv sync` into a throwaway env and assert
  `en_core_web_sm` is importable (the cycle-002 drop cannot recur). (FR-005; contract invariant.)
- [x] T005 [P] Write FAILING test `tests/deploy/test_systemd_unit.py`: render
  `deploy/ankivoice.service` substituting `{{USER}}`/`{{INSTALL_DIR}}`, assert all required
  directives ([contract](./contracts/deploy-interface.md) → unit: `Type=simple`, `User`/`Group`,
  `WorkingDirectory`, `EnvironmentFile=<dir>/.env`, `ExecStart=<dir>/.venv/bin/python -m ankivoice`,
  `Restart=on-failure`, `RestartSec=5`, `TimeoutStopSec=300`, `NoNewPrivileges=true`,
  `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`, `ReadWritePaths=<dir>`,
  `WantedBy=multi-user.target`); and, gated on `have_systemd_analyze()`, write the rendered unit to
  a temp path and assert `systemd-analyze verify` is clean. (FR-008, FR-011; research D5.)
- [x] T006 Create `deploy/ankivoice.service` template with `{{USER}}`/`{{INSTALL_DIR}}`
  placeholders exactly per [research D5](./research.md) so T005 passes. The unit declares **no**
  inbound socket/`ListenStream`/TLS directives (long-polling needs none), satisfying FR-017.
  (FR-008, FR-011, FR-017.)

**Checkpoint**: T003–T006 green ⇒ the spaCy-model retention and unit validity are proven
independent of any container.

---

## Phase 3: User Story 1 — One-command first install (Priority: P1) 🎯 MVP

**Goal**: clean host → `sudo ./install.sh` (token + archive id) → active, enabled service whose
startup preflight passed and which is polling. **Independent test**: quickstart §D in a clean
container.

### Tests (write first — MUST fail before implementation)

- [x] T007 [P] [US1] Write FAILING unit tests in `tests/deploy/test_install_unit.py` driving
  `install.sh` in a temp sandbox with a mocked PATH (stub `apt-get`, `systemctl`, `useradd`,
  `curl`, `uv`, `journalctl`): (1) refuses on non-root with a specific message and **no changes**;
  (2) refuses on an unsupported distro (fake `/etc/os-release`), no changes; (3) accepts
  `ID=debian`, `ID=ubuntu`, and `ID_LIKE=debian`; (4) creates `.env` at `0600` containing the two
  keys + `ANKIVOICE_MODEL_DIR=<dir>/models`; (5) the token never appears in stdout/stderr; (6)
  token/archive-id resolution precedence flag > env > prompt. (FR-007, FR-012, FR-015; SC-007;
  contract install.sh §1,3,4,10.)
- [x] T008 [P] [US1] Write FAILING test `tests/deploy/test_install_container.py::test_fresh_install`
  (skip if `not have_docker()`): start a privileged systemd `debian:12` container, run the one
  command with a placeholder token `--non-interactive`, then assert `systemctl is-enabled
  ankivoice` = enabled and the journal shows the preflight passing + the long-polling start line;
  assert `/opt/ankivoice/.env` is `0600` and owned by the service user. (FR-001, FR-006, FR-008,
  FR-009; SC-001, SC-004; quickstart §D.)

### Implementation (make the tests pass — the ordered installer)

- [x] T009 [US1] Implement `install.sh` core: shebang + strict mode (`set -euo pipefail`), arg/env
  parsing (`--token/--archive-id/--user/--prefix/--non-interactive/--skip-warmup/-h`), the
  **root + Debian/Ubuntu guard** ([research D1](./research.md)), and a usage/help block. Secret-safe
  logging (never `set -x` around the token; prompts to stderr). Makes T007 (1,2,3,6) pass.
  (FR-012, contract §1.)
- [x] T010 [US1] Add the provisioning steps to `install.sh`: apt-install `ffmpeg curl
  ca-certificates` (NOT espeak-ng; [D2](./research.md)); install `uv` to `/usr/local/bin` if
  missing ([D4](./research.md)); create the service user + install dir; **tar-pipe** the tree into
  the install dir with the exclude list ([D8](./research.md)); `uv sync --locked --no-dev` as the
  service user. (FR-002, FR-003, FR-004, FR-005; contract §5,6.)
- [x] T011 [US1] Add `.env` create-or-preserve to `install.sh`: if `<dir>/.env` exists, do not
  touch it; else write `0600` service-user-owned `.env` from the resolved inputs +
  `ANKIVOICE_MODEL_DIR=<dir>/models` (silent token prompt). Makes T007 (4,5) pass. (FR-007, FR-015;
  contract §3,4.)
- [x] T012 [US1] Add the warm-up step to `install.sh`: derive the **effective** cache
  ([research D7/Q2](./research.md) — preserved `.env`'s `ANKIVOICE_MODEL_DIR`, else the service
  user's default HF cache under its home; fresh `.env` ⇒ `<dir>/models`) and run
  `scripts/warmup.py` once online as the service user with `ANKIVOICE_ALLOW_DOWNLOADS=1` +
  `HF_HOME=<effective>` (skippable via `--skip-warmup`). (FR-006; SC-004.)
- [x] T013 [US1] Add unit install + start + verify to `install.sh`: render `deploy/ankivoice.service`
  → `/etc/systemd/system/ankivoice.service`, `daemon-reload`, `enable --now`, then poll the journal
  for preflight-green + the long-polling line; print a specific hint on a Telegram 401 (not an
  install failure); fail loudly on a real preflight/unit failure. Makes T008 pass. (FR-008, FR-009;
  SC-001; contract §8.)
- [x] T014 [US1] Add the success summary to `install.sh`: print copy-paste next-step hints
  (journalctl/status/restart/update/uninstall + how to send a deck). (FR-014; SC-003; contract §9.)

**Checkpoint US1**: T007–T014 green ⇒ MVP — a clean host reaches an active, enabled, preflight-green
service with one command.

---

## Phase 4: User Story 2 — Idempotent re-run to update (Priority: P2)

**Goal**: re-running the same command is safe, refreshes code/deps via a graceful restart, ends
active, and never clobbers `.env`. **Independent test**: quickstart §E.

- [x] T015 [P] [US2] Extend `tests/deploy/test_install_unit.py` with a FAILING no-clobber case:
  given an existing `.env` with arbitrary bytes, a second `install.sh` run leaves it **byte-for-byte
  identical** with unchanged perms/owner and does not prompt. (FR-010; SC-002; contract §2,3.)
- [x] T016 [P] [US2] Add `tests/deploy/test_install_container.py::test_idempotent_rerun` (skip if
  `not have_docker()`): after a fresh install, record `sha256` of `.env`, re-run
  `install.sh --non-interactive`, assert `.env` unchanged and `systemctl is-enabled` still enabled.
  (FR-010; SC-002; quickstart §E.)
- [x] T017 [US2] Make `install.sh` idempotent end-to-end: every step is a safe no-op/refresh on
  re-run (apt/uv no-op when present; tree-copy refreshes code but the exclude list spares
  `.env`/data/cache; `uv sync` reconciles; `.env` preserved; unit re-rendered;
  `systemctl enable --now` performs the graceful restart). Confirm warm-up re-validation uses the
  effective cache for a preserved `.env` ([Q2](./research.md)). Makes T015, T016 pass. (FR-010,
  FR-006; SC-002.)

**Checkpoint US2**: re-run = update; secrets preserved.

---

## Phase 5: User Story 3 — Operate the running service (Priority: P3)

**Goal**: documented, copy-pasteable logs/status/restart/update/uninstall commands.

- [x] T018 [P] [US3] Add the README "Deploy on a Debian/Ubuntu VPS" section: literal quickstart
  (clone → `sudo ./install.sh` → provide token + archive id → done), how to get the token
  (@BotFather) and the archive channel id, and the journalctl/status/restart/update(re-run)/
  uninstall commands + system requirements; reconcile any now-inaccurate manual-install wording so
  the manual path and the one-command path agree. (FR-014; SC-003.)
- [x] T019 [P] [US3] Update `.env.example` to document that the service uses
  `ANKIVOICE_MODEL_DIR=<install_dir>/models` as the offline model cache the installer sets, so
  manual operators match the service layout. (FR-014; aligns with [data-model](./data-model.md).)

**Checkpoint US3**: an operator can run every documented command as written.

---

## Phase 6: User Story 4 — Clean uninstall (Priority: P3)

**Goal**: default removes the unit; `--purge` removes app/data/cache/user, scoped to the footprint.

- [x] T020 [P] [US4] Add `tests/deploy/test_install_container.py::test_uninstall` and
  `::test_uninstall_purge` (skip if `not have_docker()`): default uninstall ⇒ unit gone, install
  dir/`.env` intact; `--purge --yes` ⇒ no install dir, no service user; assert a second uninstall
  is a no-op success. Also a unit-level FAILING test that `uninstall.sh --purge` refuses to delete
  a non-install path (scope guard). (FR-013; SC-005; contract uninstall §1–4.)
- [x] T021 [US4] Implement `uninstall.sh`: default = stop + disable + `rm` unit + `daemon-reload`;
  `--purge` (typed confirm unless `--yes`) additionally `rm -rf` the **resolved** install dir
  (refuse any path that is not the resolved `INSTALL_DIR`) + `userdel` the service user; safe to
  re-run. Never removes apt ffmpeg/uv. Makes T020 pass. (FR-013; SC-005; Constitution P5.)

**Checkpoint US4**: install is cleanly, scopedly reversible.

---

## Phase 7: Polish & Cross-Cutting (regression + full proof)

- [x] T022 Regression gate: run `uv run pytest` and confirm the app's default suite passes with the
  **same test count** as before this feature and that no `src/ankivoice/*.py` or existing test was
  changed (`git diff --stat` touches only deploy/docs/pyproject/lock); confirm `uv run pytest -m
  live` still self-skips. (FR-016, FR-018; SC-006.)
- [ ] T023 Full container proof: execute quickstart §A–H in a throwaway systemd `debian:12`
  container and record the evidence (service active/enabled + preflight green + idempotent `.env` +
  refusals + clean `--purge`) into the handoff. Run the whole `tests/deploy/` suite where docker is
  available. Confirm the running service exposes no inbound port (long-polling only). (SC-001..SC-005,
  FR-017; quickstart.)
- [x] T024 [P] Adversarial self-review (subagents) over: installer idempotency & secret hygiene
  (no clobber, `0600`, ownership, token never logged), distro/non-root refusal, the systemd unit
  (graceful stop, restart-on-failure, journald, `ReadWritePaths` scope), the offline-after-warm-up
  guarantee incl. the Q2 effective-cache path, the `en_core_web_sm` pin surviving `uv sync`, and
  uninstall scope. Fix anything confirmed; record findings. (All FRs; Constitution gates.)

---

## Dependencies & Execution Order

- **Setup (T001–T002)** → **Foundational (T003–T006)** → **US1 (T007–T014)** → **US2 (T015–T017)**
  → **US3 (T018–T019)** / **US4 (T020–T021)** → **Polish (T022–T024)**.
- T002 → T003 → T004 (lock must exist for the import-after-sync check).
- T005 → T006 (unit test before the template).
- T007/T008 (US1 tests) → T009–T014 (install.sh, built in order; T010 depends on T009; T011 on
  T010; T012 on T011; T013 on T006+T012; T014 on T013).
- US2 depends on US1's `install.sh` existing. US4's `uninstall.sh` is independent of US2/US3.
- T022 (regression) can run anytime after T003 (the only repo-state change touching lock/deps) and
  before final handoff. T023 depends on US1+US2+US4. T024 after T023.

## Parallel Opportunities

- T001 ‖ T002 (Setup).
- T004 ‖ T005 (Foundational tests, different files).
- T007 ‖ T008 (US1 tests, different files).
- T015 ‖ T016 (US2 tests).
- T018 ‖ T019 (docs) ‖ T020 (US4 test) — all different files.

## MVP Scope

**US1 (T001–T014)** alone delivers the headline value: one command from a clean Debian host to an
active, enabled, preflight-green, polling service. US2 (idempotent update), US3 (operator docs), and
US4 (clean uninstall) are incremental, independently testable additions.

## Constitution / safety notes

- No `src/ankivoice/*.py` behavior change; no existing test weakened/deleted; no `--no-verify`.
- All container work is throwaway and local; never touch the operator's VPS.
- Secrets: only `.env.example` is committed; a real token is never committed or logged.
