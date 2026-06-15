"""Core conversion pipeline (load-bearing): parse → synth → encode → package.

Synchronous and CPU-bound; the worker runs it via ``asyncio.to_thread`` (one job at a time). Within a
deck, identical cleaned sentences synthesize only ONCE (cache keyed on the full spoken string), which
keeps both CPU and the per-job disk footprint bounded (Constitution P1). When ``voice_sides="both"``
the Front question is voiced in addition to the Back answer, and the SAME cache spans both sides — a
Front whose cleaned text equals some Back (anywhere in the deck) synthesizes once. All files are
written inside ``job_dir`` so cleanup stays scoped (P5).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .audio import encode_mp3
from .packaging import MediaCard, build_apkg
from .parser import parse_deck
from .speech import Synthesizer


def build_package(
    deck_bytes: bytes,
    synthesizer: Synthesizer,
    *,
    job_dir: Path,
    max_cards: int,
    deck_name: str,
    mp3_quality: str = "4",
    ffmpeg_timeout: float = 120.0,
    voice_sides: str = "back",
) -> Path:
    """Turn raw deck bytes into a ``.apkg`` inside ``job_dir``; return its path.

    ``voice_sides`` is ``"back"`` (default — voice only the Back answer) or ``"both"`` (also voice the
    Front question). Raises :class:`ankivoice.errors.ValidationError` (from the parser) for invalid
    input — before any synthesis happens.
    """
    job_dir = Path(job_dir)
    parsed = parse_deck(deck_bytes, max_cards=max_cards)

    filename_by_spoken: dict[str, str] = {}
    media_paths: list[Path] = []
    media_cards: list[MediaCard] = []

    def audio_for(spoken: str) -> str:
        """Synthesize + encode ``spoken`` once and return its bare MP3 basename. The cache is keyed on
        the full spoken string and spans BOTH sides, so identical text is voiced once per job (P1)."""
        if spoken not in filename_by_spoken:
            # Full sha256 hexdigest (not a 16-hex prefix): a truncated prefix could collide across two
            # distinct sentences and overwrite one card's audio (cycle 002, audit A3).
            digest = hashlib.sha256(spoken.encode("utf-8")).hexdigest()
            filename = f"{digest}.mp3"
            path = job_dir / filename
            samples = synthesizer.synthesize(spoken)
            encode_mp3(samples, synthesizer.sample_rate, path, quality=mp3_quality, timeout=ffmpeg_timeout)
            filename_by_spoken[spoken] = filename
            media_paths.append(path)
        return filename_by_spoken[spoken]

    both = voice_sides == "both"
    for card in parsed.cards:
        back_audio = audio_for(card.spoken)
        front_audio = None
        # A card has Front audio only when its cleaned Front is non-empty; the empty-Front placeholder
        # is never voiced (FR-003). Cross-side dedupe falls out of the shared cache.
        if both and card.front_spoken.strip():
            front_audio = audio_for(card.front_spoken)
        media_cards.append(
            MediaCard(
                front=card.front,
                back=card.back,
                audio_filename=back_audio,
                front_audio_filename=front_audio,
            )
        )

    out_path = job_dir / f"{deck_name}.apkg"
    return build_apkg(media_cards, media_paths, out_path, deck_name=deck_name, voice_sides=voice_sides)
