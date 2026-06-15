"""Anki packaging (load-bearing). research.md Decision 3.

Builds a ``.apkg``. In the default ``back`` mode the ANSWER template renders an ``Audio`` field
containing ``[sound:<file>.mp3]``, so Anki auto-plays the audio on reveal and shows a replay button
(FR-013..016); the question template has no audio.

In ``both`` mode a SECOND audio field (``FrontAudio``) is added: the QUESTION template renders the
Front text plus ``[sound:<front>.mp3]`` (auto-play on the front + replay button), and the ANSWER
template is unchanged — it brings the front in via ``{{FrontSide}}``. Anki deliberately does NOT
auto-replay audio that arrives via ``{{FrontSide}}`` (Anki manual: "FrontSide will not automatically
play any audio that was on the front side of the card"), so the front audio does NOT re-blast on
reveal while still exposing its replay button; the back audio (placed directly in the answer) does
auto-play. The two modes use DISTINCT, deterministic model ids so both kinds of deck coexist on
import. Original Front/Back text is placed in the note verbatim (FR-012). Media are bundled by
filesystem path; the note references them by bare basename.
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import genanki

# Deterministic, hard-coded ids (chosen once) so re-imports update rather than duplicate (research.md).
MODEL_ID = 1607392319        # back-only note type (3 fields)
MODEL_ID_BOTH = 1989327411   # both-sides note type (4 fields) — distinct so the two coexist on import
DECK_ID = 2059400110

# back mode: question side has NO audio; answer side shows the Back plus the [sound:] field (auto-play).
QFMT = "{{Front}}"
AFMT = "{{FrontSide}}<hr id=answer>{{Back}}<br>{{Audio}}"
# both mode: question side shows the Front plus its [sound:] (auto-play on the front). The conditional
# section keeps an empty-Front-audio card free of a stray <br>/sound. The answer template is the SAME
# as back mode (front comes via {{FrontSide}}, which Anki does not auto-replay — no re-blast).
QFMT_BOTH = "{{Front}}{{#FrontAudio}}<br>{{FrontAudio}}{{/FrontAudio}}"
AFMT_BOTH = AFMT
CSS = ".card{font-family:arial;font-size:20px;text-align:center;color:black;background-color:white;}"

_FALLBACK_NAME = "AnkiVoice deck"
# An Anki card whose question side renders empty is NOT generated (no studyable card). When the Front
# is empty (allowed — e.g. a blanked/cloze prompt; FR-003) we show a neutral placeholder so a card is
# always produced. Only the empty case is affected; non-empty Fronts are preserved verbatim (FR-012).
_FRONT_PLACEHOLDER = "(no prompt — reveal the answer)"


@dataclass(frozen=True)
class MediaCard:
    front: str  # original, preserved (display)
    back: str  # original, preserved (display)
    audio_filename: str  # bare basename used inside the Back [sound:...]
    # bare basename for the Front [sound:...] in both mode; None ⇒ this card has no Front audio
    # (back mode, an empty Front, or a Front that cleans to whitespace). Defaults to None so back-mode
    # construction (and existing call sites) are unchanged.
    front_audio_filename: str | None = None


def output_name(original_filename: str | None) -> str:
    """Derive a deck/file base name from the user's filename stem, with a generic fallback (FR-031)."""
    if original_filename:
        stem = Path(original_filename).name  # drop any directory
        stem = Path(stem).stem.strip()
        if stem:
            return stem
    return _FALLBACK_NAME


def _build_model() -> genanki.Model:
    return genanki.Model(
        MODEL_ID,
        "AnkiVoice Audio",
        fields=[{"name": "Front"}, {"name": "Back"}, {"name": "Audio"}],
        templates=[{"name": "Card 1", "qfmt": QFMT, "afmt": AFMT}],
        css=CSS,
    )


def _build_model_both() -> genanki.Model:
    return genanki.Model(
        MODEL_ID_BOTH,
        "AnkiVoice Audio (both sides)",
        fields=[{"name": "Front"}, {"name": "Back"}, {"name": "Audio"}, {"name": "FrontAudio"}],
        templates=[{"name": "Card 1", "qfmt": QFMT_BOTH, "afmt": AFMT_BOTH}],
        css=CSS,
    )


def build_apkg(
    cards: Sequence[MediaCard],
    media_paths: Sequence[Path],
    out_path: Path | str,
    *,
    deck_name: str,
    voice_sides: str = "back",
) -> Path:
    """Build the ``.apkg`` at ``out_path`` and return it.

    In ``back`` mode (default) each card becomes a 3-field note ``[Front, Back, "[sound:<back>]"]``.
    In ``both`` mode each card becomes a 4-field note ``[Front, Back, "[sound:<back>]", front]`` where
    ``front`` is ``"[sound:<front_audio_filename>]"`` (or empty when the card has no Front audio).
    ``media_paths`` are the filesystem paths bundled as media (each card's Back — and, in both mode,
    Front — basename must match one of them).
    """
    # Every card's [sound:] basename MUST have a bundled media file, else the card would ship with
    # broken audio (cycle 002, audit E1). Fail loudly here rather than produce a silently-broken deck.
    # In both mode the Front [sound:] is checked the same way.
    media_basenames = {Path(p).name for p in media_paths}
    required = {c.audio_filename for c in cards}
    required |= {c.front_audio_filename for c in cards if c.front_audio_filename}
    missing = sorted(b for b in required if b not in media_basenames)
    if missing:
        raise ValueError(
            f"card audio file(s) not bundled as media: {missing} (media has {sorted(media_basenames)})"
        )

    both = voice_sides == "both"
    model = _build_model_both() if both else _build_model()
    deck = genanki.Deck(DECK_ID, deck_name)
    for index, card in enumerate(cards):
        front = card.front if card.front.strip() else _FRONT_PLACEHOLDER
        # guid includes the row index so two identical export rows stay distinct cards (one card per
        # usable input row) rather than collapsing on import via an identical fields-derived guid. The
        # 002 guid scheme is unchanged for BOTH modes (Front audio is a function of the Front, already
        # captured), so back-mode output stays byte-identical.
        fields = [front, card.back, f"[sound:{card.audio_filename}]"]
        if both:
            front_sound = f"[sound:{card.front_audio_filename}]" if card.front_audio_filename else ""
            fields.append(front_sound)
        deck.add_note(
            genanki.Note(
                model=model,
                fields=fields,
                guid=genanki.guid_for(deck_name, str(index), card.front, card.back, card.audio_filename),
            )
        )
    package = genanki.Package(deck)
    package.media_files = [str(p) for p in media_paths]
    out_path = Path(out_path)
    # genanki's write_to_file uses tempfile.mkstemp() and never removes its temp SQLite DB. Point the
    # temp dir at the job dir (out_path's parent) for the write so that leftover lands INSIDE the job
    # dir and is removed by scoped cleanup — keeping disk flat (cycle 002, audit B1). Builds are
    # serialized (one synthesis/packaging at a time), so this brief global override is safe; restore it.
    saved_tempdir = tempfile.tempdir
    tempfile.tempdir = str(out_path.parent)
    try:
        package.write_to_file(str(out_path))
    finally:
        tempfile.tempdir = saved_tempdir
    return out_path
