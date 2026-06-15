<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
at specs/003-one-command-deploy/plan.md (the active one-command install/deploy
increment — packaging & deployment only, app behaviour unchanged), which builds on
the quality/bug-fix/performance increment at specs/002-quality-bugfix-perf/plan.md
and the product spec/plan at specs/001-ankivoice-audio-decks/plan.md. All describe
one consistent, reconciled behaviour; where they discuss the same topic the latest
increment's artifacts are authoritative.
<!-- SPECKIT END -->

## Both-sides voicing

By **default** AnkiVoice voices BOTH sides of each card (`ANKIVOICE_VOICE_SIDES=both`): the Front
question is voiced in addition to the Back answer. The question side auto-plays the front audio (with a
replay button) and the answer side auto-plays the back audio (with a replay button). The answer pulls
the front in via `{{FrontSide}}`, which Anki does **not** auto-replay, so the front audio never
re-blasts on reveal. An empty Front is never voiced (the card stays back-only). Identical text on a
Front and a Back synthesizes once. Set `ANKIVOICE_VOICE_SIDES=back` to voice only the Back answer
(byte-identical to the original output). The two modes use distinct, deterministic note-type ids so the
two kinds of deck coexist on import. See `specs/002-quality-bugfix-perf/contracts/changes.md` →
"both-sides voicing".
