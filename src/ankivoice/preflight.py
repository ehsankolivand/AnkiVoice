"""Fail-fast startup guard (correctness guard, NOT deployment tooling).

This guard runs once at startup, before any job is accepted, and refuses to start with a clear, specific
message if it cannot produce correct audio — turning an invisible corruption / late failure into an
obvious, actionable startup error.

It checks two things:

1. **ffmpeg** is on PATH — it is invoked as a separate subprocess to encode MP3s; a missing ffmpeg fails
   the first encode otherwise.
2. The **phonemizer + configured voice/model** actually synthesize, by running a one-word probe that
   includes an **out-of-dictionary** token so it exercises misaki's espeak fallback. This catches a
   broken phonemizer (e.g. a missing/incompatible bundled ``espeakng_loader`` library) or an uncached
   voice/model offline, AND **prewarms** the model so the first job pays no cold-start (IR-008..011).

Note (cycle 002, verified): misaki loads ``espeak-ng`` from a **bundled** shared library via the
``espeakng_loader`` Python dependency (``EspeakWrapper.set_library(...)``), NOT from a PATH binary —
synthesis of out-of-dictionary words works with no ``espeak-ng`` on PATH. So gating startup on
``shutil.which("espeak-ng")`` would be a false-positive that wrongly refuses a working host; the probe
synthesis is the correct ground-truth check. Set ``ANKIVOICE_SKIP_PREFLIGHT`` to bypass (tests/dev).
"""

from __future__ import annotations

import os
import shutil

from .config import Config

# Includes an out-of-dictionary proper noun so the probe exercises misaki's espeak fallback (not just the
# dictionary path) — a broken phonemizer surfaces here rather than silently at the first real job.
_PROBE_TEXT = "Warmup Zbigniew."


class PreflightError(Exception):
    """Raised when a hard runtime dependency for producing correct audio is missing/misconfigured."""


def check_runtime(config: Config, synthesizer) -> None:
    """Verify ffmpeg is on PATH and the phonemizer + configured voice/model synthesize offline.

    ``synthesizer`` is the same instance the worker will use, so the probe both verifies availability
    and warms the model. Raises :class:`PreflightError` (naming the specific problem) on failure.
    No-op if ``ANKIVOICE_SKIP_PREFLIGHT`` is set.
    """
    if os.environ.get("ANKIVOICE_SKIP_PREFLIGHT"):
        return

    if shutil.which("ffmpeg") is None:
        raise PreflightError(
            "ffmpeg was not found on PATH. It is REQUIRED to encode audio. Install it (e.g. "
            "`apt-get install ffmpeg` or `brew install ffmpeg`) and restart."
        )

    # Ground-truth check for the phonemizer + configured voice/model — and prewarm (IR-008..011).
    try:
        synthesizer.synthesize(_PROBE_TEXT)
    except Exception as exc:  # broken espeakng_loader lib, LocalEntryNotFoundError offline, missing weights…
        raise PreflightError(
            f"The speech engine could not synthesize with the configured voice "
            f"{config.default_voice!r} (lang {config.lang_code!r}) offline: {exc}. This means the "
            f"phonemizer (bundled espeak-ng) or the voice/model is unavailable. Run "
            f"`uv run python scripts/warmup.py` once (online) to download the model and the voice, then "
            f"restart. Set ANKIVOICE_ALLOW_DOWNLOADS=1 to permit downloads at startup."
        ) from exc
