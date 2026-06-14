<!--
SYNC IMPACT REPORT
==================
Version change: (template, unversioned) → 1.0.0
Bump rationale: Initial ratification of the AnkiVoice constitution. MAJOR baseline (1.0.0).

Principles defined (all new):
- I.   Resource-Bounded by Design (P1)
- II.  Agent-Native Architecture (P2)
- III. Additive, Non-Breaking Evolution (P3)
- IV.  Local-First, Offline Audio (P4)
- V.   Always Clean Up, Only Within Scope (P5)
- VI.  Durable, Resumable, Fair Jobs (P6)
- VII. Test-First for Load-Bearing Paths (NON-NEGOTIABLE) (P7)
- VIII.Config and Secrets via Environment Only (P8)

Added sections:
- Core Principles (8 principles)
- Resource & Operational Constraints (formerly SECTION_2)
- Development Workflow & Quality Gates (formerly SECTION_3)
- Governance

Removed sections: none (template placeholders fully replaced).

Templates / artifacts reviewed for alignment:
- .specify/templates/plan-template.md ...... ✅ aligned (Constitution Check gate is constitution-driven; no edit needed)
- .specify/templates/spec-template.md ...... ✅ aligned (tech-agnostic spec sections compatible)
- .specify/templates/tasks-template.md ..... ✅ aligned (TDD enforced per-feature via Principle VII at /speckit-tasks time)
- CLAUDE.md (agent context) ................. ✅ managed SPECKIT block updated by /speckit-plan + agent-context hook
- README.md ................................. ⚠ pending (created during implementation; must reference these principles)

Follow-up TODOs: none. No bracket tokens deferred.
-->

# AnkiVoice Constitution

AnkiVoice is a Telegram bot that turns a user's text-based Anki deck into an
audio-enhanced Anki package, generating native-accent English speech locally on a
single-core, ~4 GB VPS. These principles are the non-negotiable contract for how
the system is designed, built, and extended.

## Core Principles

### I. Resource-Bounded by Design (P1)

- The service MUST run reliably within a fixed budget of one shared CPU core,
  ~4 GB RAM, and ~40 GB disk (the target is a $6/mo single-core VPS).
- Exactly ONE speech-synthesis job MUST run at any instant. Additional requests
  MUST wait in a durable queue, never run concurrently.
- Under load the system MUST degrade predictably — it slows down and queues — and
  MUST NOT crash, exhaust memory, or fill the disk.
- The service MUST be safe to expose publicly under bursty, adversarial load.

Rationale: On a tiny shared host, predictable degradation beats peak throughput.
A bounded, serialized pipeline is what keeps a public bot alive instead of OOM-killed.

### II. Agent-Native Architecture (P2)

- Each module MUST encapsulate exactly one responsibility (parsing/validation,
  speech, packaging, queue+worker, Telegram handlers, delivery+cleanup, config).
- Each module MUST be simultaneously a code-generation unit and an agent-context
  unit: understandable, testable, and extendable in isolation without reading the
  whole system.
- Cross-module coupling MUST be expressed through small, explicit interfaces.

Rationale: This project is built and extended primarily by coding agents; modules
sized to fit one agent's working context are faster and safer to change correctly.

### III. Additive, Non-Breaking Evolution (P3)

- New capability MUST NOT break the core pipeline: ingest → synthesize → package →
  deliver.
- Every addition MUST ship with tests proving the end-to-end pipeline still works.
- Changes that would break the pipeline contract MUST be redesigned to be additive,
  or surfaced as an explicit breaking change requiring a constitution-governed
  decision — never slipped in silently.

Rationale: A bot people rely on must keep working as it grows; regression tests on
the pipeline are the proof that "additive" is real and not aspirational.

### IV. Local-First, Offline Audio (P4)

- Speech MUST be generated locally and offline using an on-host model.
- There MUST be no per-request cloud cost for synthesis.
- User text MUST NOT leave the server by default. The only outbound transfers are
  to Telegram (the requesting user) and the operator-owned archive destination.

Rationale: Local-first synthesis removes per-request cost and protects user data;
offline operation is what makes the bot cheap and private to run.

### V. Always Clean Up, Only Within Scope (P5)

- Every temporary and output file MUST be removed after delivery, on BOTH the
  success AND the failure path, so disk usage stays flat over time.
- Deletion MUST be scoped strictly to the job's own working directory and its own
  outputs.
- Deletion MUST NEVER touch any path outside that job's working directory. Cleanup
  code MUST verify scope before unlinking.

Rationale: Unbounded disk growth is the most likely way this service dies on a
40 GB host; scoped deletion guarantees we never destroy anything we did not create.

### VI. Durable, Resumable, Fair Jobs (P6)

- Job state MUST be persisted durably so that a process restart resumes pending and
  in-flight work rather than losing it.
- Each Telegram user MUST have at most one active job at a time.
- Jobs MUST be processed in arrival order (fair, first-come-first-served).

Rationale: A bot on a cheap host will be restarted; durable, resumable, per-user
fair queuing is what makes restarts and bursts safe instead of lossy or abusable.

### VII. Test-First for Load-Bearing Paths (NON-NEGOTIABLE) (P7)

- Strict TDD applies to every behavior of the load-bearing paths: write the failing
  test first, run it and watch it fail for the right reason, write the minimal code
  to pass, then refactor with the test green.
- Load-bearing paths are: the deck parser, the speech-synthesis wrapper, the Anki
  packager, the SQLite job queue and worker (including restart-resume), the
  delivery-and-cleanup step, and one end-to-end "sample deck in → importable package
  with playable audio out" test.
- The default test suite MUST be fast and fully offline: the Kokoro model and the
  Telegram network MUST be faked so the suite needs neither.
- Exactly one clearly-marked, self-skipping live test MUST exercise real Kokoro
  synthesis and a real .apkg import end to end, kept OUT of the default run.
- Tests MUST NOT be disabled, deleted, or bypassed (no `--no-verify`) to make a run
  pass.

Rationale: These paths are where correctness, data integrity, and resource safety
live; test-first is the only way to know they work and stay working as agents extend
the code. A fast offline suite keeps the feedback loop tight on a tiny host.

### VIII. Config and Secrets via Environment Only (P8)

- Bot token, archive chat/channel destination, default voice, per-job limits
  (e.g. max cards), working directory, and database path MUST be read from
  environment/config.
- Secrets MUST NEVER be hard-coded or committed to the repository.
- The repository MUST ship an `.env.example` documenting every configuration key.

Rationale: Environment-only config keeps secrets out of source control and lets the
same build run safely across the operator's machines without code changes.

## Resource & Operational Constraints

- Resource ceiling: 1 shared CPU core, ~4 GB RAM, ~40 GB disk. Designs that assume
  GPUs, multiple cores running synthesis in parallel, or paid cloud speech are
  prohibited (see Principles I and IV).
- Concurrency: a single speech worker. Synthesis is serialized; only the
  delivery/upload step MAY overlap with the next job's synthesis.
- Persistence: the only datastore is the SQLite job store. No additional databases,
  caches, or services may be introduced for v1 (see Governance simplicity rule).
- Data flow out: only to the requesting Telegram user and the operator archive
  destination; never to third-party services.
- The user's original card text MUST be preserved exactly; audio is added, nothing
  is rewritten.

## Development Workflow & Quality Gates

- Test-first discipline (Principle VII) is the primary quality gate. A load-bearing
  behavior is "done" only when its failing-first test now passes and the full suite
  is green.
- Pipeline regression gate (Principle III): no change merges unless the end-to-end
  pipeline test still passes.
- Cleanup gate (Principle V): any code path that writes job files MUST be matched by
  scoped cleanup on both success and failure, verified by tests.
- Resource gate (Principle I): new work MUST NOT introduce unbounded concurrency,
  unbounded memory growth, or unbounded disk growth.
- Scope discipline: build only what the specification describes. Speculative
  abstractions, alternative backends, and out-of-scope features are rejected.

## Governance

- This constitution supersedes other practices. When guidance conflicts, the
  constitution wins.
- On any conflict BETWEEN principles, the principle wins over convenience, and the
  conflict MUST be surfaced explicitly in the spec/plan — never silently resolved.
- Simplicity rule: prefer the simplest design that satisfies all principles. Added
  complexity MUST be justified against a principle it serves.
- Amendment procedure: changes to this document MUST be documented (what changed and
  why) and accompanied by a semantic version bump:
  - MAJOR: backward-incompatible principle removal or redefinition.
  - MINOR: a new principle or materially expanded guidance.
  - PATCH: clarifications and wording fixes with no semantic change.
- Compliance review: every plan runs a Constitution Check gate; every analysis pass
  treats a constitution conflict as CRITICAL and requires fixing the spec, plan, or
  tasks — not diluting the principle.

**Version**: 1.0.0 | **Ratified**: 2026-06-14 | **Last Amended**: 2026-06-14
