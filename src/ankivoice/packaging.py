"""Anki packaging (load-bearing). research.md Decision 3.

Builds a ``.apkg`` whose ANSWER template renders an ``Audio`` field containing ``[sound:<file>.mp3]``,
so Anki auto-plays the audio on reveal and shows a replay button (FR-013..016). The question template
has no audio. Original Front/Back text is placed in the note verbatim (FR-012). Media are bundled by
filesystem path; the note references them by bare basename.
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import genanki

# Deterministic, hard-coded ids (chosen once) so re-imports update rather than duplicate (research.md).
MODEL_ID = 1607392319
DECK_ID = 2059400110

# Question side has NO audio; answer side shows the original Back plus the [sound:] field (auto-play).
QFMT = "{{Front}}"
AFMT = "{{FrontSide}}<hr id=answer>{{Back}}<br>{{Audio}}"
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
    audio_filename: str  # bare basename used inside [sound:...]


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


def build_apkg(
    cards: Sequence[MediaCard],
    media_paths: Sequence[Path],
    out_path: Path | str,
    *,
    deck_name: str,
) -> Path:
    """Build the ``.apkg`` at ``out_path`` and return it.

    Each card becomes a note ``[Front, Back, "[sound:<audio_filename>]"]``. ``media_paths`` are the
    filesystem paths bundled as media (each basename must match a card's ``audio_filename``).
    """
    # Every card's [sound:] basename MUST have a bundled media file, else the card would ship with
    # broken audio (cycle 002, audit E1). Fail loudly here rather than produce a silently-broken deck.
    media_basenames = {Path(p).name for p in media_paths}
    missing = sorted({c.audio_filename for c in cards if c.audio_filename not in media_basenames})
    if missing:
        raise ValueError(
            f"card audio file(s) not bundled as media: {missing} (media has {sorted(media_basenames)})"
        )

    model = _build_model()
    deck = genanki.Deck(DECK_ID, deck_name)
    for index, card in enumerate(cards):
        front = card.front if card.front.strip() else _FRONT_PLACEHOLDER
        # guid includes the row index so two identical export rows stay distinct cards (one card per
        # usable input row) rather than collapsing on import via an identical fields-derived guid.
        deck.add_note(
            genanki.Note(
                model=model,
                fields=[front, card.back, f"[sound:{card.audio_filename}]"],
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
