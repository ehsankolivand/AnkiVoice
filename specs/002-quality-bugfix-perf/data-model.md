# Data Model: AnkiVoice cycle 002 (deltas vs 001)

Only the changes versus [001 data-model](../001-ankivoice-audio-decks/data-model.md) are listed; the
001 data model otherwise stands. The single datastore remains the SQLite job store (Constitution: no
additional datastore/cache).

## Job (persisted) — extended

Two additive durable columns make delivery idempotent across restarts (IR-014):

| Field | Type | Notes |
|---|---|---|
| `archive_sent` | INTEGER (0/1) NOT NULL DEFAULT 0 | Set 1 once the operator-archive copy is sent. |
| `user_sent` | INTEGER (0/1) NOT NULL DEFAULT 0 | Set 1 once the requesting-user copy is sent. |

Migration: additive `ALTER TABLE jobs ADD COLUMN ... DEFAULT 0` guarded so reopening an old DB is safe
(check `PRAGMA table_info`). Both flags are set to 0 only at **enqueue**; `requeue_in_progress` never
resets them (a rebuilt job must remember what already went out).

## JobState — corrected (PACKAGING removed)

```
queued        -> claimed by the worker (FCFS)
synthesizing  -> the serialized, one-at-a-time CPU step (parse → synth → encode → package)
uploading     -> delivering: archive copy first, then user copy (overlaps the next synthesis)
delivered     -> both copies sent; ready to clean
cleaned       -> working dir + outputs removed (TERMINAL, success)
failed        -> processing error; friendly reason recorded; files cleaned (TERMINAL)
```

Happy path: `queued → synthesizing → uploading → delivered → cleaned`. Error path:
`queued/synthesizing/uploading → failed → scoped cleanup`. `PACKAGING` is removed from the enum, the
DB-string domain, and all derived sets — packaging is part of the single `synthesizing` CPU step and was
never meaningfully observable as a distinct state (audit D3). The worker sets `uploading`
**synchronously** the instant `build_package` returns, so at most one job is ever `synthesizing`.

Derived state sets (store):
- **Active** (one-active-per-user, FR-020): `{queued, synthesizing, uploading, delivered}`.
- **Rebuildable** (resume requeues): `{synthesizing, uploading}` (+ legacy `'packaging'` mapped here).
- **Ahead** (queue position, FR-018): `{queued, synthesizing}` — a job that has finished synthesis and is
  uploading/delivering no longer counts ahead of a queued job.

## Resume (corrected, exactly-once)

On startup `worker.resume()`:
1. `prune_terminal_jobs(keep=ANKIVOICE_JOB_HISTORY)` — bound the table (IR-013).
2. `requeue_in_progress()` — reset rebuildable states (incl. legacy `'packaging'`) to `queued`
   **without** touching the delivery flags.
3. clean `delivered`-but-not-`cleaned` jobs (remove dir → `cleaned`); they are never requeued
   (double-send guard, already correct).
4. fail `abandoned uploads` (input never saved) so the user is unblocked.

A requeued `uploading` job is rebuilt and re-delivered, but `deliver()` skips any copy whose flag is set
→ only the missing copy is sent → **exactly-once** across a mid-delivery crash (IR-014, SC-005).

## ParsedDeck → Card (clarified, not structurally changed)

- `front` (display): the field a normal import shows — transport quotes unwrapped **only when balanced**;
  BOM stripped; line endings normalized to `\n`; otherwise byte-for-byte. MAY be empty.
- `back` (display): same fidelity rule as `front`.
- `spoken`: `html.unescape(balanced-unwrap(back))`. A card is **usable** only if `spoken.strip() != ""`
  (a Back that cleans to whitespace is skipped + counted — IR-004).
- `front_spoken` (additive, both-sides voicing): `html.unescape(balanced-unwrap(front))`, the cleaned
  Front spoken form. Empty/whitespace ⇒ the card has **no Front audio** (the empty-Front placeholder is
  never voiced). Used only when `ANKIVOICE_VOICE_SIDES=both`; the display `front` is unchanged.
- `skipped_empty_back`: counts rows skipped for **any** non-usable reason — empty Back, no-TAB line, or
  blank-after-clean (the field name is retained for compatibility; its meaning is "skipped, not voiceable").

## New configuration keys (env only — Principle VIII)

| Key | Default | Meaning |
|---|---|---|
| `ANKIVOICE_JOB_HISTORY` | `500` | Max retained terminal (cleaned/failed) job rows; older pruned at startup (IR-013). |
| `ANKIVOICE_FFMPEG_TIMEOUT` | `120` | Seconds before an MP3 encode is aborted with a clear error (IR-018). |
| `ANKIVOICE_DELIVERY_RETRIES` | `3` | Bounded delivery attempts (with backoff) before deferring to restart (IR-015). |
| `ANKIVOICE_SKIP_PREFLIGHT` | unset | Test/dev escape hatch to skip the startup guard (IR-008..011). |
| `ANKIVOICE_VOICE_SIDES` | `both` | `both` = voice the Front question and Back answer (default); `back` = voice the Back only (byte-identical to the original output). Case-insensitive; unknown ⇒ ConfigError. |

Already-present-but-now-documented (audit G12): `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE` are defaulted
to `1` by the entrypoint unless `ANKIVOICE_ALLOW_DOWNLOADS` is set (e.g. for warm-up). `ANKIVOICE_MODEL_DIR`
maps to `HF_HOME`.

## Startup preflight (new transient entity)

`preflight.check_runtime(config, synthesizer)` → raises `PreflightError(message)` if ffmpeg is not on
PATH, or the configured voice/model + phonemizer cannot synthesize a one-word **out-of-dictionary** probe
offline (which also prewarms the model). espeak-ng is **bundled** (espeakng_loader, loaded in-process by
misaki), so it is NOT gated on PATH — the probe synthesis is the ground-truth check. Skipped iff
`ANKIVOICE_SKIP_PREFLIGHT` is set. Not persisted; runs once before `run_polling`.
