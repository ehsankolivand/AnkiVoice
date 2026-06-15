# AnkiVoice Quality/Bugfix/Perf Cycle — Progress Log

Feature branch: `002-quality-bugfix-perf` (main untouched).
Goal: fix every real bug, reconcile spec↔code↔contract drift into one consistent story,
add a fail-fast startup guard, and profile→optimize the hot path without breaking invariants.
Deployment/easy-install is OUT OF SCOPE this cycle.

## Baseline (verified)
- Default suite GREEN: 82 passed, 1 deselected (`uv run pytest`, 1.31s).
- Tooling present: ffmpeg, ffprobe, espeak-ng on PATH.
- Model cached: hexgrad/Kokoro-82M (kokoro-v1_0.pth + voices/af_heart.pt), spaCy en_core_web_sm. Live test runnable.
- genanki 0.13.1 confirms: EMPTY front → 0 studyable cards (note.cards=0); placeholder front → 1 card. (Finding D constraint is REAL.)

## Phase status
- [x] Read entire repo + all spec artifacts + all tests (complete manual model built)
- [x] Branch created
- [~] Deep adversarial audit (parallel workflow wf_45c835d2-340 running) → audit-notes.md
- [x] Performance profiling DONE → perf-notes.md (synthesis 93%, model-bound on 1 core; cross-job cache REJECTED by constitution; keep per-job dedupe)
- [x] Deep adversarial audit DONE (46 confirmed, 4 rejected) → audit-notes.md
- [x] /speckit-specify DONE → spec.md (IR-001..IR-020, 6 stories, SC-001..008; checklist 16/16)
- [x] /speckit-plan DONE → plan.md, research.md, data-model.md, contracts/changes.md, quickstart.md; Constitution Check PASS; cross-job cache REJECTED; CLAUDE.md refreshed
- [x] /speckit-clarify DONE → 5 decisions self-resolved + encoded in spec.md Clarifications; plan still consistent; checklist 16/16
- [x] /speckit-tasks DONE → tasks.md (T001..T041, test-first, by story US1..US6)
- [x] /speckit-analyze DONE → 100% coverage, 0 critical/high, 3 LOW (C1 fixed: config→foundational T006b); constitution clean
- [~] /speckit-implement: US1 (parser/pipeline/packaging) DONE; US2 (preflight/main) DONE; US3
  (temp-leak/prune) DONE; US4 (delivery idempotency/retry/atomic enqueue) DONE; US5 (audio timeout/
  memoize/inference_mode; perf reconciled — Kokoro non-deterministic, "byte-identical" dropped) DONE.
  Suite 110 green. NEXT: US6 reconcile 001 artifacts; Polish (field-count test, full suite, live test, self-review).
- [x] /speckit-implement DONE: US1–US6 + polish; all 42 tasks [x]; default suite 111 passed +1 live green;
  all 001 artifacts + CLAUDE.md reconciled; FR-026/clarification updated for bounded retry.
- [~] Self-review (parallel workflow wf_9acc0bf3-be6 running)
- [ ] Commit + handoff

## Findings model (from manual audit; confirm via parallel audit)
- A. Resume re-delivers DELIVERED: CODE already correct (store requeues only rebuildable; worker.resume cleans DELIVERED). tasks.md T022/T023 WORDING is stale (claims DELIVERED→QUEUED). Robust fix NOT done: add per-copy delivery flags (archive_sent/user_sent) so a mid-delivery crash re-sends only the missing copy (idempotent deliver()).
- B. One-active-per-user race: bot reserves slot via enqueue(PENDING_INPUT) BEFORE download (race largely closed by single-thread asyncio). Store has NO atomic check+insert; data-model claims "enqueue rejected" but store.enqueue never refuses. Harden: atomic enqueue_if_no_active in store; bot uses it; on refusal delete orphan + message.
- C. espeak-ng missing silently drops words: NO startup guard exists. Add fail-fast preflight (espeak-ng, ffmpeg, model+voice offline).
- D. Empty-front placeholder: constraint REAL (verified). Keep minimal placeholder; update spec FR-003/edge cases to record deliberate deviation.
- E. Line-ending normalization (\r\n,\r → \n) mutates text: reconcile — amend FR-012/SC-003 to state line endings normalized to LF (documented).
- F. guid: CODE already per-row+content+deck stem (distinct rows kept, stable re-import). research.md Decision 3 wording (content-hash) is stale. Reconcile research↔packaging.
- G. CSV-quote display: CODE strips transport quotes for BOTH display+spoken via csv reader (matches a real Anki import). Reconcile FR-012/SC-003 "byte-for-byte" to mean the decoded field (what Anki imports), not raw transport bytes. Fix clean_for_speech contract wording (it only html.unescapes; csv reader does the unwrap).
- H. Module count drift: plan says "12 modules" but pipeline.py makes 13. Fix plan + contracts consistently.
- I. warmup voice coupling: warmup.py ALREADY reads ANKIVOICE_DEFAULT_VOICE. Mostly resolved; add preflight check of configured voice + doc.
- J. Unbounded job-table growth: jobs never pruned. Add bounded prune of old terminal jobs at startup; document.
- K. Dead PACKAGING state: set AFTER build completes (misleading). It DOES serve "moved out of SYNTHESIZING before next claim" purpose. Fix: remove PACKAGING; worker sets UPLOADING synchronously after build. Update enum/_ACTIVE/_AHEAD/_REBUILDABLE + all artifacts/tests.
- L. Late validation UX: add a fast format sniff at upload (reject obviously-malformed immediately); keep full validation in worker.
- M. Failed delivery holds slot until restart: add a bounded in-process delivery retry (few attempts, backoff) before deferring to restart. Keep simple.
