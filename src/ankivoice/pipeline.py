"""Core conversion pipeline (load-bearing): parse → synth → encode → package.

Synchronous and CPU-bound; the worker runs it via ``asyncio.to_thread`` (one job at a time). Within a
deck, identical cleaned sentences synthesize only ONCE (cache keyed on ``sha256(spoken)``), which keeps
both CPU and the per-job disk footprint bounded (Constitution P1). All files are written inside
``job_dir`` so cleanup stays scoped (P5).
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
) -> Path:
    """Turn raw deck bytes into a ``.apkg`` inside ``job_dir``; return its path.

    Raises :class:`ankivoice.errors.ValidationError` (from the parser) for invalid input — before any
    synthesis happens.
    """
    job_dir = Path(job_dir)
    parsed = parse_deck(deck_bytes, max_cards=max_cards)

    filename_by_spoken: dict[str, str] = {}
    media_paths: list[Path] = []
    media_cards: list[MediaCard] = []

    for card in parsed.cards:
        if card.spoken not in filename_by_spoken:
            # Full sha256 hexdigest (not a 16-hex prefix): a truncated prefix could collide across two
            # distinct sentences and overwrite one card's audio (cycle 002, audit A3).
            digest = hashlib.sha256(card.spoken.encode("utf-8")).hexdigest()
            filename = f"{digest}.mp3"
            path = job_dir / filename
            samples = synthesizer.synthesize(card.spoken)
            encode_mp3(samples, synthesizer.sample_rate, path, quality=mp3_quality)
            filename_by_spoken[card.spoken] = filename
            media_paths.append(path)
        media_cards.append(
            MediaCard(front=card.front, back=card.back, audio_filename=filename_by_spoken[card.spoken])
        )

    out_path = job_dir / f"{deck_name}.apkg"
    return build_apkg(media_cards, media_paths, out_path, deck_name=deck_name)
