# Feature Specification: One-Command Install & Deployment

**Feature Branch**: `003-one-command-deploy`

**Created**: 2026-06-15

**Status**: Draft

**Input**: User description: "One-command install & deployment for AnkiVoice on a fresh Debian/Ubuntu VPS — a new operator goes from a clean host to a running, auto-restarting bot with one command, after providing only their bot token and archive id. Packaging and deployment only; the app's runtime behavior is unchanged."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One-command first install on a clean host (Priority: P1)

A new operator has a fresh Debian/Ubuntu VPS and the AnkiVoice repository on it. They run a
single command, are asked only for their Telegram bot token and archive chat/channel id (or
supply them up front), and a few minutes later the bot is running as a managed background
service that is enabled to start on boot. They did not have to know anything about Python,
virtual environments, system services, or the app internals.

**Why this priority**: This is the entire point of the feature and the minimum viable product.
Without it, none of the other stories matter. On its own it makes AnkiVoice deployable by a
non-expert on a cheap VPS, which is the goal.

**Independent Test**: On a clean Debian container, run the one install command with a token and
archive id supplied; confirm the run ends with a background service that is *active* and
*enabled-on-boot*, whose startup self-check passed and which has begun polling — with no further
manual steps required. (Because a placeholder token cannot complete a real Telegram login, the
independent test asserts the service reached and passed its startup self-check and began
long-polling; a real operator-supplied token keeps it continuously active.)

**Acceptance Scenarios**:

1. **Given** a clean supported host with the repository present and a valid bot token and
   archive id available, **When** the operator runs the single install command, **Then** the
   system installs everything it needs, performs the one-time model warm-up, creates the
   operator configuration with owner-only permissions, and registers, enables, and starts a
   background service whose startup self-check passes.
2. **Given** the same install in progress, **When** the operator did not pass the token/archive
   id as arguments and no configuration exists yet, **Then** the installer prompts for exactly
   those two values and proceeds; it never invents, hard-codes, or echoes the secret.
3. **Given** a completed install, **When** the operator reboots the host, **Then** the service
   starts again automatically without any manual action.
4. **Given** a completed install, **When** the operator inspects the service, **Then** its logs
   are visible through the host's standard system log and show the startup self-check passing
   and the bot polling.

---

### User Story 2 - Idempotent re-run to update, preserving secrets (Priority: P2)

An operator who already installed AnkiVoice wants to apply an update (new code pulled into the
repository) or simply re-run the installer after a hiccup. They run the exact same single
command again. It completes safely, the service keeps working, and their existing secret
configuration is left exactly as it was — never overwritten, never printed.

**Why this priority**: Updating and recovering are the second-most-common operator actions after
the first install. Making the installer safe to re-run removes the need for any separate update
procedure and protects the operator's secrets.

**Independent Test**: After a successful install, record the configuration file's contents and
permissions, re-run the install command, and confirm the run succeeds, the service is still
active, and the configuration file is byte-for-byte identical with unchanged owner-only
permissions.

**Acceptance Scenarios**:

1. **Given** a host with AnkiVoice already installed and configured, **When** the operator
   re-runs the install command, **Then** it completes successfully and the existing
   configuration file is unchanged (same content, same owner, same owner-only permissions).
2. **Given** an already-installed host, **When** the operator re-runs the installer to pick up
   updated application code, **Then** the dependencies and the service are refreshed and the
   service ends the run active again.
3. **Given** a re-run, **When** the installer needs the two required values and they already
   exist in the configuration, **Then** it does not prompt for them again and does not require
   re-entry.

---

### User Story 3 - Operate the running service (logs, status, restart, update) (Priority: P3)

An operator running the bot needs to see what it is doing, restart it, check whether it is
healthy, and update it — using short, documented, copy-pasteable commands, without learning the
internals.

**Why this priority**: Day-2 operations. The bot is useful only if the operator can observe and
manage it; these commands turn a running process into something an operator can actually live
with.

**Independent Test**: Following only the documented commands, an operator can view the service
logs, see its current status, restart it, and update it; each documented command runs as written
on the target host.

**Acceptance Scenarios**:

1. **Given** a running install, **When** the operator runs the documented "view logs" command,
   **Then** they see the service's recent and live log output.
2. **Given** a running install, **When** the operator runs the documented "restart" command,
   **Then** the service stops gracefully (finishing or safely parking any in-flight deck) and
   comes back up.
3. **Given** a running install, **When** the operator runs the documented "status" command,
   **Then** they see whether the service is active and enabled-on-boot.

---

### User Story 4 - Clean uninstall (Priority: P3)

An operator wants AnkiVoice gone. They run an uninstall command: by default it removes the
managed service and leaves their data and configuration in place; with an explicit opt-in it
also removes the application files, the data, the downloaded model cache, and the dedicated
service account, leaving the host tidy. It never touches anything outside AnkiVoice's own
footprint.

**Why this priority**: A trustworthy install must be cleanly reversible. Operators are far more
willing to try software that they know they can fully remove.

**Independent Test**: After an install, run the uninstall command and confirm no managed service
remains; run it again with the explicit full-removal opt-in and confirm no application, data,
model-cache files, or service account remain, and that nothing outside the app footprint was
removed.

**Acceptance Scenarios**:

1. **Given** an installed host, **When** the operator runs uninstall without the full-removal
   opt-in, **Then** the managed service is stopped, disabled, and removed, while the install
   directory, data, and configuration remain on disk.
2. **Given** an installed host, **When** the operator runs uninstall with the explicit
   full-removal opt-in, **Then** the service, the install directory, the data, the model cache,
   and the dedicated service account are all removed, and no residual AnkiVoice files remain.
3. **Given** uninstall in any mode, **When** it runs, **Then** it removes only paths within the
   app's own footprint and never deletes unrelated host files.

---

### Edge Cases

- **Unsupported OS**: On a non-Debian/Ubuntu or non-Linux host, the installer refuses early with
  a clear, specific message and makes no changes.
- **Not run as root/sudo**: When invoked without the privilege needed to create a service
  account, install a system package, and register a boot service, the installer refuses with
  guidance and makes no changes.
- **Existing configuration on re-run**: The installer must detect an existing configuration and
  preserve it exactly rather than prompting again or overwriting it.
- **Missing required values on first install**: If the two required values are neither passed as
  arguments nor already present, the installer obtains them (prompt) and refuses to start a
  service with placeholder/empty secrets.
- **Interrupted install**: If the install is interrupted (e.g. during the model warm-up) and
  re-run, the re-run completes the setup without corrupting an existing configuration.
- **Startup self-check fails** (e.g. the model warm-up did not complete, or the audio encoder is
  missing): the install surfaces the specific failure clearly rather than leaving a silently
  broken service.
- **Graceful stop during an in-flight deck**: On stop/restart/uninstall, an in-progress deck is
  given a bounded window to finish or to be safely parked for resume, so no job is lost or
  corrupted.
- **No outbound network at install time**: If the one-time online steps (system package, runtime
  toolchain, model warm-up) cannot reach the network, the install fails clearly at that step
  rather than producing a half-installed service.
- **Existing config missing a newer key on update**: An older configuration created before this
  feature may lack keys the install would otherwise set (e.g. the model-cache location). The
  installer preserves that file unchanged and still produces a service that synthesizes offline,
  by aligning the warm-up to whatever cache the preserved configuration will actually use.

## Clarifications

### Session 2026-06-15

- Q: When the install command is re-run to update, what does it refresh, and is it zero-downtime?
  → A: It refreshes the application code, the Python dependencies, and the service definition, and
  ensures the system package and runtime toolchain are present (it does not force-upgrade ones
  already installed). It applies the update by gracefully restarting the service — a brief,
  bounded downtime during which any in-flight deck is finished or safely parked and then resumed —
  and the run ends with the service active and enabled. "Keeps the service running" means it ends
  active, not that there is zero downtime.
- Q: On an update where an existing configuration file is preserved unchanged, how does the
  installer guarantee the service still synthesizes offline (the warm-up must populate the cache
  the service will actually read)?
  → A: The installer never injects or rewrites keys in an existing configuration file. Instead it
  derives the warm-up's cache location from that file's effective configuration (the operator's
  model-cache setting if present, otherwise the service account's default cache, which lives under
  the install directory), so the warm-up always populates the exact cache the running service
  reads. Only a freshly created configuration file has the cache location set explicitly to a
  stable path under the install directory.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST let an operator install AnkiVoice and bring it online with a single
  command run on a fresh supported host, after the operator supplies only a bot token and an
  archive chat/channel id.
- **FR-002**: The installer MUST install the system dependency required to encode audio if it is
  not already present.
- **FR-003**: The installer MUST ensure the application's runtime toolchain is present, installing
  it if missing, without requiring the operator to install it beforehand.
- **FR-004**: The installer MUST run the bot under a dedicated, non-root service account and from a
  fixed install location, with the account name and install location overridable through a small
  set of documented variables that have sane defaults.
- **FR-005**: The installer MUST provision the application's dependencies reproducibly, including
  the English language model the speech engine needs, such that re-running the install (update)
  does not drop that language model.
- **FR-006**: The installer MUST perform the one-time, online model warm-up during install so that
  the running service can synthesize audio fully offline afterward, populating the **same** cache
  location the running service will read. When an existing configuration is preserved, the
  installer MUST derive that cache location from the preserved configuration's effective settings
  (never mutating the file) so warm-up and runtime always agree.
- **FR-007**: The installer MUST create or validate the operator configuration as a file readable
  only by the service account (owner-only permissions, owned by the service account), accepting
  the two required values via command argument, interactive prompt, or a pre-existing
  configuration file, and MUST NOT hard-code, log, or echo the secret.
- **FR-008**: The installer MUST register the bot as a managed background service and enable and
  start it so that it runs immediately and again automatically on every boot.
- **FR-009**: The installer MUST run the application's existing fail-fast startup self-check as
  part of bringing the service up and MUST surface a clear pass/fail result.
- **FR-010**: Re-running the install command MUST be safe and idempotent and MUST be the supported
  way to update an install; it MUST refresh the application code, the Python dependencies, and the
  service definition (ensuring, but not force-upgrading, the system package and runtime toolchain),
  apply the change via a graceful restart, end with the service active and enabled, and MUST
  preserve any existing operator configuration byte-for-byte and never overwrite or expose it.
- **FR-011**: The managed service MUST restart automatically on failure with a sensible backoff,
  MUST send its logs to the host's standard system log, and MUST stop gracefully within a bounded
  window long enough for an in-flight deck to finish or be safely parked for resume.
- **FR-012**: On an unsupported operating system, or when run without the privilege it requires,
  the installer MUST refuse early with a clear, specific, actionable message and make no changes
  to the host.
- **FR-013**: The system MUST provide an uninstall command that stops, disables, and removes the
  managed service by default, and — only behind an explicit operator opt-in — also removes the
  install directory, the data, the model cache, and the dedicated service account; uninstall MUST
  remove only paths within the app's own footprint.
- **FR-014**: The system MUST document, in copy-pasteable form for the target host, how to obtain
  the bot token and the archive id, the single install command, and the commands to view logs,
  check status, restart, update, and uninstall the service, plus the system requirements.
- **FR-015**: The repository MUST ship only a no-secrets example configuration template; a real
  secret MUST NEVER be committed to the repository or written to logs.
- **FR-016**: This feature MUST NOT change any observable behavior of the bot, its synthesized
  output, or any runtime invariant (single CPU core, exactly one synthesis at a time, scoped
  cleanup, flat disk usage, offline-after-warm-up, the fail-fast startup guard); it only packages,
  installs, and supervises the existing application.
- **FR-017**: In steady state the running service MUST require no inbound network connectivity, no
  TLS, and no reverse proxy; the install command MUST be the only step that uses the network.
- **FR-018**: The application's existing automated test suite MUST remain green and unchanged, and
  the new packaging artifacts (installer behavior, service-unit validity, language-model
  retention) MUST themselves be covered by automated checks.

### Key Entities *(include if feature involves data)*

- **Service account**: A dedicated, non-root host account that owns and runs the bot; the least
  privilege needed to operate, owning the install directory, configuration, data, and model cache.
- **Install directory**: A fixed location holding the application code, its provisioned
  dependencies, the operator configuration, the data, and the model cache.
- **Operator configuration**: The environment-only settings file containing the two required
  secrets/values plus optional overrides; owner-only readable, never committed, never logged.
- **Managed service**: The boot-enabled, auto-restarting, journald-logged supervised process that
  runs the bot and stops gracefully.
- **Model cache**: The on-disk location, populated by the one-time warm-up, that lets the service
  synthesize speech offline; readable by the service account.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a clean supported host, a brand-new operator runs exactly one command, provides
  only the bot token and archive id, and ends with a managed background service that is active and
  enabled-on-boot, whose startup self-check passed and which has begun polling — with zero
  additional manual steps.
- **SC-002**: Re-running the install command on an already-installed host completes successfully,
  ends with the service active and enabled (a brief graceful restart applies the update; an
  in-flight deck is finished or safely parked and resumed, never lost), and leaves the operator's
  existing configuration file byte-for-byte unchanged with unchanged owner-only permissions.
- **SC-003**: Using only the documented commands, an operator can view the service's logs, check
  its status, restart it, update it, and uninstall it; each documented command runs as written.
- **SC-004**: After the install-time warm-up, the running service synthesizes audio with no
  network access available.
- **SC-005**: A default uninstall leaves no managed service; a full-removal uninstall additionally
  leaves no application, data, model-cache files, or service account — and neither mode removes
  anything outside the app's footprint.
- **SC-006**: The application's existing automated test suite passes unchanged, and the bot's
  observable behavior is identical to before this feature.
- **SC-007**: Installing on an unsupported operating system, or without the required privilege,
  stops with a clear, specific refusal and changes nothing on the host.

## Assumptions

- Target host class: Debian 12 (bookworm) or a recent Ubuntu LTS, single shared CPU core, ~4 GB
  RAM, x86_64, with outbound internet available during install only. Other Linux distributions and
  non-Linux hosts are explicitly unsupported and met with a clear refusal. The install logic is
  architecture-independent; only the install-time model warm-up and the audio-encoder package are
  fetched per-architecture by their standard package tooling.
- The installer runs with root/sudo, which it needs to create a service account, install a system
  package, and register a boot service. Non-root invocation is refused with guidance.
- The operator can obtain a bot token from Telegram's @BotFather and an archive chat/channel id;
  the install accepts these via command argument, interactive prompt, or a pre-existing
  configuration file, and never invents or hard-codes them.
- "Single command" means one shell command the operator runs after obtaining the repository on the
  host (e.g. via git clone); obtaining the repository is a documented prerequisite, not part of the
  command's own scope.
- The bot is a long-polling Telegram client and therefore needs no inbound connectivity; this is a
  fixed fact of the application, not a configurable option introduced here.
- The application already provides: an environment-only configuration with two required keys and
  the rest defaulted; a one-time online warm-up routine; a fail-fast startup self-check; and
  graceful handling of a stop signal that finishes or parks an in-flight job. This feature reuses
  those rather than re-implementing them.
- Containerization remains optional and is never required for the primary native install path.
