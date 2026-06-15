# Performance Profiling — AnkiVoice hot path

Representative deck: 60 rows, 50 unique sentences (10 duplicate rows to exercise per-job dedupe),
full English sentences (~3s audio each). Measured on this dev host with the REAL Kokoro-82M model,
`torch.set_num_threads(1)` (single-core budget), `HF_HUB_OFFLINE=1` (offline). `/tmp/ankivoice_perf/deck.txt`.

## BEFORE (baseline, current code)

| Stage | Time | Notes |
|---|---|---|
| import torch+kokoro | 0.62 s | one-time per process |
| model load (+1st synth) | 5.60 s | one-time per process (lazy, in worker thread) |
| **synthesis** | **35.0 s** | 50 unique × ~700 ms/sentence (median 679 ms) — **93% of compute** |
| MP3 encode | 2.71 s | 50 × ~54 ms (ffmpeg subprocess) — 7% of compute |
| **build_package total** | **37.99 s** | 60 rows → 50 synth (dedupe saved 10) + 50 encode + package; out 1.15 MB |

- Synthesis real-time factor ≈ 0.22× (0.22 s compute per 1 s audio). `synth/(synth+encode) = 93%`.
- **Per-job dedupe already saves ~17%** here (10 of 60 rows are duplicates → 10×700ms ≈ 7 s not re-synthesized).

## What is NOT a win (measured, rejected)
- `torch.inference_mode()` / `no_grad`: kokoro's model already applies `@torch.no_grad()` internally.
  inference_mode measured 697→657 ms median (~3%, within noise). Harmless but not a real lever.
- **Batching** all unique sentences into ONE `pipeline()` call (vs 50 calls): **0% faster** (32.45s vs 32.30s).
  Per-call Python/G2P overhead is already negligible; time is pure model inference. Rejected (adds
  chunk↔sentence alignment risk for multiline fields, no benefit).
- Overlapping ffmpeg encode with synthesis: on a single shared core they just timeshare; encode is only
  7%; adds complexity. Rejected.

## Constitution decision — CROSS-JOB AUDIO CACHE: REJECTED
The brief floats an optional bounded, size-capped, LRU cross-job audio cache (voice+sha256(spoken)) as the
highest-leverage win for sentences repeated *across* decks. **It fails the Constitution Check** and is
rejected: the constitution's Resource & Operational Constraints state *"the only datastore is the SQLite
job store. **No additional databases, caches, or services may be introduced for v1**"* and Principle V
requires disk to *"stay flat over time"* with deletion scoped to a job's own dir. A persistent cross-job
cache is an additional cache that persists outside any job dir → prohibited. **Decision: keep per-job
sha256 dedupe only.** (Per the brief: "otherwise keep per-job dedupe only.")

## AFTER (with cycle-002 safe optimizations)

| Stage | Before | After | Notes |
|---|---|---|---|
| build_package (60 rows, 50 unique) | 37.99 s | **38.61 s** | within run-to-run noise — synthesis is model-bound (see below) |
| mp3 files produced | 50 | 50 | per-job dedupe preserved; full-digest filenames |

The headline build time is **unchanged within measurement noise**: synthesis (≈93% of compute) is
model-bound on one core and cannot be safely reduced, and the safe micro-opts (inference_mode, memoized
ffmpeg path, full-digest filenames) remove redundant work that is small relative to inference. The real
lever — per-job dedupe — is preserved (10 duplicate rows here skip re-synthesis, ≈17%). The only larger
lever (cross-job cache) is rejected on Constitution grounds (below). This is the honest, expected
outcome under the single-core + offline + flat-disk + no-cache constraints: correctness and the resource
bound are never traded for speed.

## IMPORTANT — the speech engine is NON-DETERMINISTIC per call (measured)

Two synthesis runs of the *same* text produce *different* PCM (maxdiff ≈0.06–0.13), with the same
variance whether or not `torch.inference_mode()` is used. Therefore:
- "byte-identical audio" is **not** an achievable/meaningful criterion; the correct criterion is that the
  audio-generation **computation** is unchanged (same model/voice/params/code path). The 002 spec
  (IR-016/SC-006/US5) and research(001) are reconciled to say this.
- `inference_mode` is kept anyway: it is standard inference best-practice, byte-output-neutral in
  *character*, and removes residual autograd/version bookkeeping (micro, within noise here).
- Non-determinism does NOT affect correctness elsewhere: MP3 filenames key on the *text* (sha256(spoken)),
  the per-note guid keys on *text* (deck+index+content), and dedupe is per-job on *text* — all stable.
  Display TEXT is fully deterministic and exactly preserved.

## Safe optimizations to apply (preserve all invariants)
1. Resolve the ffmpeg binary path ONCE (memoized) instead of `shutil.which("ffmpeg")` per encoded
   sentence — removes a redundant PATH scan per unique sentence. (audio.py)
2. Wrap synthesis inference in `torch.inference_mode()` — zero risk, removes residual autograd/version
   bookkeeping over the model's existing no_grad. (speech.py)
3. Drop a redundant numpy re-conversion in `speech.synthesize`. (speech.py)
4. Keep + regression-pin the per-job sha256 dedupe (the actual main lever; already in test_pipeline).

Conclusion: the hot path is per-sentence Kokoro inference on one core and is **model-bound / irreducible
under the single-core + offline + flat-disk + no-cache constraints**. The headline deck time is dominated
by synthesis that cannot be safely reduced; the safe wins are the existing per-job dedupe plus small
redundant-work removals. AFTER numbers recorded post-implementation.
