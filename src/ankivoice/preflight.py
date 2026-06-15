"""Fail-fast startup guard (correctness guard, NOT deployment tooling).

A missing ``espeak-ng`` makes the phonemizer **silently drop out-of-dictionary words** from the audio
(verified; research 001). A missing ``ffmpeg`` fails the first encode. An uncached configured voice/model
fails the first job offline with a generic error. This guard runs once at startup, before any job is
accepted, and refuses to start with a clear, specific message if it cannot produce correct audio —
turning an invisible corruption / late failure into an obvious, actionable startup error.

Probing the configured voice with a one-word real synthesis also **prewarms** the model so the first
job does not pay the cold-start load (IR-011). Set ``ANKIVOICE_SKIP_PREFLIGHT`` to bypass (tests/dev).
"""

from __future__ import annotations

import os
import shutil

from .config import Config

_PROBE_TEXT = "warmup"


class PreflightError(Exception):
    """Raised when a hard runtime dependency for producing correct audio is missing/misconfigured."""


def check_runtime(config: Config, synthesizer) -> None:
    """Verify espeak-ng + ffmpeg are on PATH and the configured voice/model synthesize offline.

    ``synthesizer`` is the same instance the worker will use, so the probe both verifies availability
    and warms the model. Raises :class:`PreflightError` (naming the specific missing item) on failure.
    No-op if ``ANKIVOICE_SKIP_PREFLIGHT`` is set.
    """
    if os.environ.get("ANKIVOICE_SKIP_PREFLIGHT"):
        return

    if shutil.which("espeak-ng") is None:
        raise PreflightError(
            "espeak-ng was not found on PATH. It is REQUIRED — without it the phonemizer silently "
            "drops out-of-dictionary words from the audio. Install it (e.g. `apt-get install "
            "espeak-ng` or `brew install espeak-ng`) and restart."
        )
    if shutil.which("ffmpeg") is None:
        raise PreflightError(
            "ffmpeg was not found on PATH. It is REQUIRED to encode audio. Install it (e.g. "
            "`apt-get install ffmpeg` or `brew install ffmpeg`) and restart."
        )

    # Ground-truth availability check for the configured voice/model — and prewarm (IR-010, IR-011).
    try:
        synthesizer.synthesize(_PROBE_TEXT)
    except Exception as exc:  # LocalEntryNotFoundError offline, missing weights, etc.
        raise PreflightError(
            f"Could not synthesize with the configured voice {config.default_voice!r} "
            f"(lang {config.lang_code!r}) offline: {exc}. Run `uv run python scripts/warmup.py` once "
            f"(online) to download the model and the voice, then restart. Set ANKIVOICE_ALLOW_DOWNLOADS=1 "
            f"to permit downloads at startup."
        ) from exc
