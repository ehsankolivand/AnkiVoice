"""One-time online warm-up so the bot can then run fully offline (Constitution P4, research.md).

Downloads the Kokoro-82M weights, the default voice pack, and (on first English synthesis) the spaCy
``en_core_web_sm`` G2P model into the HuggingFace cache. After this completes you can run the bot with
``HF_HUB_OFFLINE=1`` and no network is needed for synthesis.

Run:  uv run python scripts/warmup.py
Honors ANKIVOICE_DEFAULT_VOICE, ANKIVOICE_LANG_CODE, ANKIVOICE_MODEL_DIR (no bot token required).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    voice = os.environ.get("ANKIVOICE_DEFAULT_VOICE", "af_heart")
    lang = os.environ.get("ANKIVOICE_LANG_CODE", "a")
    model_dir = os.environ.get("ANKIVOICE_MODEL_DIR")
    if model_dir:
        os.environ.setdefault("HF_HOME", model_dir)

    # Ensure spaCy English model (misaki's G2P needs it); install if missing.
    try:
        import spacy  # noqa: F401

        try:
            import en_core_web_sm  # noqa: F401
        except ImportError:
            print("Installing spaCy en_core_web_sm (one time)…")
            import subprocess

            subprocess.run([sys.executable, "-m", "spacy", "download", "en_core_web_sm"], check=True)
    except Exception as exc:  # pragma: no cover - best effort
        print(f"Note: could not pre-install spaCy model ({exc}); misaki may fetch it on first run.")

    from ankivoice.speech import KokoroSynthesizer

    print(f"Warming up Kokoro (voice={voice!r}, lang={lang!r})… downloading weights + voice…")
    synth = KokoroSynthesizer(
        voice=voice, lang_code=lang, model_dir=Path(model_dir) if model_dir else None
    )
    samples = synth.synthesize("This is a warm-up sentence to download the model and the voice pack.")
    print(f"OK: synthesized {len(samples)} samples at {synth.sample_rate} Hz.")
    print(f"Cache location: {os.environ.get('HF_HOME', '~/.cache/huggingface')}")
    print("You can now run the bot offline with HF_HUB_OFFLINE=1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
