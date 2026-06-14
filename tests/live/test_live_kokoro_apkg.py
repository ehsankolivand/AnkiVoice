"""T039 — LIVE end-to-end with the REAL Kokoro model and a REAL .apkg build.

Marked ``live`` and DESELECTED by default (pyproject ``addopts = -m 'not live'``). Run explicitly:

    uv run pytest -m live

Self-skips when ffmpeg/espeak-ng are missing or the Kokoro model/voice/G2P model are not available
offline (so it never breaks the default suite or CI without the model cached).
"""

import json
import shutil
import zipfile

import pytest

pytestmark = pytest.mark.live


def _missing_system_deps() -> str | None:
    for tool in ("ffmpeg", "espeak-ng"):
        if shutil.which(tool) is None:
            return f"{tool} not on PATH"
    return None


def test_live_real_synthesis_and_apkg(tmp_path):
    missing = _missing_system_deps()
    if missing:
        pytest.skip(missing)

    from ankivoice.pipeline import build_package
    from ankivoice.speech import KokoroSynthesizer

    synth = KokoroSynthesizer(voice="af_heart", lang_code="a")
    try:
        samples = synth.synthesize("Hello there, this is a real Kokoro synthesis test.")
    except Exception as exc:  # model/voice/G2P not available offline → skip, do not fail
        pytest.skip(f"Kokoro model/voice not available: {exc}")

    assert samples.dtype.kind == "f" and samples.ndim == 1 and len(samples) > 0
    assert synth.sample_rate == 24000

    job_dir = tmp_path / "job_live"
    job_dir.mkdir()
    raw = b"greeting\tHello there, how are you today?\nthanks\tThank you so much for your help.\n"
    apkg = build_package(raw, synth, job_dir=job_dir, max_cards=10, deck_name="live", mp3_quality="4")

    assert apkg.exists() and apkg.stat().st_size > 0
    with zipfile.ZipFile(apkg) as z:
        names = z.namelist()
        assert any(n.startswith("collection.anki2") for n in names)
        media = json.loads(z.read("media"))
        assert len(media) == 2  # two real MP3s
        # each bundled media entry is a real, non-empty MP3
        for numbered in media:
            assert len(z.read(numbered)) > 0

    # the produced MP3s are valid (ffprobe says codec=mp3)
    mp3s = list(job_dir.glob("*.mp3"))
    assert len(mp3s) == 2
    import subprocess

    for mp3 in mp3s:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name", "-of", "default=nw=1", str(mp3)],
            capture_output=True, text=True,
        )
        assert "codec_name=mp3" in probe.stdout
