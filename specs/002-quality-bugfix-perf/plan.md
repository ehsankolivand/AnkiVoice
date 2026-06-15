# Implementation Plan: AnkiVoice — Quality, Bug-Fix & Performance Increment

**Branch**: `002-quality-bugfix-perf` | **Date**: 2026-06-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/002-quality-bugfix-perf/spec.md`

## Summary

A reconciliation + hardening + safe-speedup pass over the existing AnkiVoice service (product spec
[001](../001-ankivoice-audio-decks/spec.md)). It fixes every confirmed defect in
[audit-notes.md](./audit-notes.md) (CSV row-swallowing & display-quote stripping, UTF-8 BOM, truncated
MP3-filename collision, blank-spoken cards, ffmpeg hang, the genanki system-temp leak), adds the
mandated fail-fast startup guard (espeak-ng + ffmpeg + configured voice/model offline), makes delivery
exactly-once across restarts (per-copy flags) with bounded retry, bounds the job table, applies only
invariant-safe speedups (measured in [perf-notes.md](./perf-notes.md)), and reconciles all
spec↔code↔contract drift into one consistent story. **Strict TDD**: each fix lands as a failing
regression test first, then the minimal change. The default suite stays fast and fully offline; the
single live test stays self-skipping. Deployment / easy-install is explicitly out of scope.

## Technical Context

**Language/Version**: Python 3.12 (`>=3.12,<3.13`), `uv` (unchanged from 001).

**Primary Dependencies**: unchanged — kokoro 0.9.4 (Kokoro-82M, CPU), genanki 0.13.1, soundfile 0.14.0,
numpy 2.4.6, python-telegram-bot[ext] 22.8; system ffmpeg (libmp3lame) + espeak-ng; dev pytest 9.1.0,
pytest-asyncio 1.4.0, pytest-mock 3.15.1. **No new runtime or dev dependency is added this increment.**

**Storage**: the single SQLite job store (unchanged — no new datastore/cache). The `jobs` table gains
two boolean columns (`archive_sent`, `user_sent`) via an additive, backward-compatible migration.

**Testing**: pytest. Default suite fast + fully offline (FakeSynthesizer + FakeSender; Kokoro/Telegram
never loaded or contacted). One `@pytest.mark.live` self-skipping test exercises real Kokoro + a real
`.apkg`. New regression tests are added per fix; no existing test is weakened or deleted.

**Target Platform**: single shared CPU core, ~4 GB RAM, ~40 GB disk ($6/mo VPS); dev/CI on macOS.

**Project Type**: single project — long-polling chat-bot service.

**Performance Goals**: same-or-faster on the representative deck with byte-identical audio; no new
concurrency; synthesis stays serialized and single-core. Measured baseline in perf-notes.md (synthesis
= 93% of compute, model-bound).

**Constraints**: 1 core (`torch.set_num_threads(1)` + `torch.inference_mode()`), ~4 GB RAM (model loaded
once; per-sentence streaming + per-job dedupe), ~40 GB disk (**strictly flat** — now including engine
temp files; bounded job table), offline synthesis, env-only config.

**Scale/Scope**: unchanged caps (`MAX_CARDS`=200, `MAX_FILE_BYTES`=2 MB); new bounded limits
(terminal-record retention, encode timeout, delivery retry count/backoff) with safe env defaults.

## Constitution Check

*GATE: must pass before Phase 0 and re-checked after Phase 1.* This increment **strengthens** every
principle; no violations. The one principle-driven design decision (cross-job cache) is resolved against
the constitution below.

| Principle | How this increment satisfies (and strengthens) it | Verdict |
|---|---|---|
| **I. Resource-Bounded** | Keeps single-worker, one-synthesis-at-a-time, `set_num_threads(1)`. Adds `inference_mode()`; bounds the job table (was unbounded); adds an encode timeout so a stuck encoder can't hang the worker; bounded delivery retry (no unbounded loop). | PASS (stronger) |
| **II. Agent-Native** | Adds ONE small single-responsibility module (`preflight.py`); every change is local to one module behind its existing interface; contracts updated to match. | PASS |
| **III. Additive, Non-Breaking** | The end-to-end pipeline test stays green throughout; every fix ships with a test; behavior changes are deliberate, spec-recorded, and pinned. | PASS |
| **IV. Local-First, Offline** | The startup guard *enforces* offline readiness (configured voice/model cached) and fails fast otherwise; no new outbound destination; no cloud. | PASS (stronger) |
| **V. Always Clean Up, Scoped** | Fixes the genanki **system-temp leak** so disk is now *truly* flat; cleanup stays scoped to the job dir (the scope assertion is unchanged). | PASS (stronger) |
| **VI. Durable, Resumable, Fair** | Per-copy delivery flags make resume **exactly-once** (removes the documented mid-delivery duplicate); atomic one-active-per-user enqueue; FCFS unchanged; bounded retry. | PASS (stronger) |
| **VII. Test-First (NON-NEGOTIABLE)** | Every bug fix and reconciled behavior is a failing-first regression test then minimal code; suite stays fast/offline; live test self-skips. | PASS |
| **VIII. Config/Secrets via Env** | New limits read from `ANKIVOICE_*` env with safe defaults; `.env.example` updated; no secrets added. | PASS |

**Cross-job audio cache — GATE DECISION: REJECTED.** The brief floats an optional bounded, size-capped,
LRU cross-job audio cache as the highest-leverage speedup. It **fails** the Constitution Check: the
Resource & Operational Constraints state *"the only datastore is the SQLite job store. No additional
databases, caches, or services may be introduced for v1,"* and Principle V requires flat disk with
deletion scoped to a job's own dir — a persistent cross-job cache is an additional cache that persists
outside any job dir. Per the brief ("otherwise keep per-job dedupe only"), we **keep per-job sha256
dedupe only**. Recorded in Complexity Tracking and perf-notes.md.

**Post-Phase-1 re-check**: design artifacts add no new datastore/cache, no extra concurrency, no
out-of-scope feature; the two new `jobs` columns are additive; `preflight.py` is one small module →
still PASS.

## Project Structure

### Documentation (this feature)

```text
specs/002-quality-bugfix-perf/
├── plan.md              # This file (/speckit-plan command output)
├── spec.md              # Increment requirements (IR-001..IR-020)
├── audit-notes.md       # Confirmed findings + decisions (the recorded audit)
├── perf-notes.md        # Measured profiling + the rejected cross-job cache
├── research.md          # Phase 0 — fix/reconciliation decisions (this command)
├── data-model.md        # Phase 1 — Job extension + corrected state machine
├── quickstart.md        # Phase 1 — how to validate the increment
├── contracts/
│   └── changes.md       # The exact interface/behavior deltas vs the 001 contracts
├── checklists/
│   └── requirements.md  # Spec quality checklist (16/16)
└── tasks.md             # Phase 2 — /speckit-tasks output (test-first; NOT created by /speckit-plan)
```

### Source Code (repository root) — changes only

```text
src/ankivoice/
├── preflight.py     # NEW: fail-fast startup guard (espeak-ng + ffmpeg + voice/model offline)
├── parser.py        # CHANGE: line-by-line first-TAB split; balanced-only unwrap; utf-8-sig (BOM);
│                    #         clean_for_speech does unwrap+unescape; skip blank-spoken rows
├── pipeline.py      # CHANGE: full-digest MP3 filenames (no truncation collision)
├── audio.py         # CHANGE: memoized ffmpeg path; subprocess timeout
├── speech.py        # CHANGE: torch.inference_mode() around synthesis
├── packaging.py     # CHANGE: scope genanki temp into job_dir; [sound:]↔media basename assertion
├── store.py         # CHANGE: drop PACKAGING; archive_sent/user_sent columns + setters;
│                    #         enqueue_if_no_active (atomic); prune_terminal_jobs; _AHEAD/_ACTIVE/_REBUILDABLE
├── models.py        # CHANGE: JobState without PACKAGING; Job gains archive_sent/user_sent
├── delivery.py      # CHANGE: idempotent per-copy send (skip already-sent); set flags
├── worker.py        # CHANGE: set UPLOADING (not PACKAGING) after build; bounded delivery retry;
│                    #         resume maps legacy 'packaging'; prune at resume
├── bot.py           # CHANGE: use enqueue_if_no_active; orphan-cleanup on refusal
└── __main__.py      # CHANGE: call preflight before run_polling

scripts/warmup.py    # (unchanged behavior; doc clarified — already reads the configured voice)
tests/               # NEW regression tests across unit/integration; existing tests updated for the
                     # reconciled state machine + fidelity; live test unchanged (self-skipping)
.env.example         # CHANGE: document new keys + the offline env vars
specs/001-ankivoice-audio-decks/*  # RECONCILED to match the corrected code (one consistent story)
CLAUDE.md            # CHANGE: managed SPECKIT block refreshed
```

**Structure Decision**: Keep the flat single-responsibility package (Constitution II). The only new
module is `preflight.py` (one responsibility: startup readiness). Everything else is an in-place fix
behind the existing module interface, so the agent-native boundaries are preserved.

## Approach (how the work is sequenced)

1. **Regression-test-first per fix** (Constitution VII): for each finding, write the failing test that
   reproduces it (RED), then the minimal fix (GREEN), then refactor. Group by module to keep diffs small.
2. **Reconcile drift in lockstep with the code**: when a fix changes behavior (parser fidelity, state
   machine, guid wording, fidelity rule), update spec/plan/data-model/research/contracts/tasks/CLAUDE.md
   in the same change so no artifact is ever left contradicting the code.
3. **Startup guard** (`preflight.py`): pure, unit-tested with mocked `which`/cache probes; wired into
   `__main__` before `run_polling`; doubles as model prewarm.
4. **Profile-then-optimize**: baseline already measured (perf-notes.md). Apply only the invariant-safe
   wins (`inference_mode`, memoized ffmpeg path, full-digest filenames, keep dedupe). Re-measure on the
   same representative deck and record before/after. Reject the cross-job cache (Constitution).
5. **Full suite green + live test** before handoff; final adversarial self-review.

## Phase 0 — Outline & Research

Complete — see [research.md](./research.md): the per-finding decisions (parser fidelity model, BOM,
guid, state-machine simplification, delivery idempotency, startup-guard probe strategy, temp-file
scoping, bounded prune/retry, the invariant-safe perf set) with rationale and rejected alternatives,
all verified against the installed packages and the measured profile.

## Phase 1 — Design & Contracts

Complete — [data-model.md](./data-model.md) (Job extension: `archive_sent`/`user_sent`; corrected
JobState without PACKAGING; corrected resume/queue-position; bounded retention),
[contracts/changes.md](./contracts/changes.md) (the exact deltas to the 001 module interfaces and the
bot contract), [quickstart.md](./quickstart.md) (validation of each user story). Agent context
(`CLAUDE.md`) managed block refreshed to reference this plan.

## Complexity Tracking

No constitution violations. Deliberate rejections recorded for traceability:

| Considered | Decision | Why the simpler/none option wins |
|---|---|---|
| Cross-job LRU audio cache (voice+sha256) | **Rejected** | Constitution forbids additional caches/datastores in v1 and requires flat disk; per-job dedupe already removes intra-deck redundancy; synthesis is model-bound so the cache only helps cross-deck repeats — not worth violating a principle. Keep per-job dedupe only. |
| Split synthesis/packaging into two observable states | **Rejected** | Synthesis+packaging are one CPU step; a separate PACKAGING state was never meaningfully observed → removed rather than faked (simplest correct model). |
