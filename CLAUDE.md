<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
at specs/002-quality-bugfix-perf/plan.md (the active quality/bug-fix/performance
increment), which builds on the product spec/plan at
specs/001-ankivoice-audio-decks/plan.md. Both describe one consistent, reconciled
behaviour; where they discuss the same topic the 002 artifacts are authoritative.
<!-- SPECKIT END -->

## Both-sides voicing (additive feature)

Optionally voice BOTH sides of each card. Set `ANKIVOICE_VOICE_SIDES=both` (default `back` = voice the
Back answer only, byte-identical to the original output). In `both` mode the Front question is also
voiced: the question side auto-plays the front audio (with a replay button) and the answer side
auto-plays the back audio (with a replay button). The answer pulls the front in via `{{FrontSide}}`,
which Anki does **not** auto-replay, so the front audio never re-blasts on reveal. An empty Front is
never voiced (the card stays back-only). Identical text on a Front and a Back synthesizes once. Both
modes use distinct, deterministic note-type ids so the two kinds of deck coexist on import. See
`specs/002-quality-bugfix-perf/contracts/changes.md` → "both-sides voicing".
