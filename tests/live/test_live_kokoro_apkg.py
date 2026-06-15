"""T039 — LIVE end-to-end with the REAL Kokoro model and a REAL .apkg build.

Marked ``live`` and DESELECTED by default (pyproject ``addopts = -m 'not live'``). Run explicitly:

    uv run pytest -m live

Self-skips when ffmpeg/espeak-ng are missing or the Kokoro model/voice/G2P model are not available
offline (so it never breaks the default suite or CI without the model cached).
"""

import json
import shutil
import sqlite3
import zipfile

import pytest

pytestmark = pytest.mark.live


def _missing_system_deps() -> str | None:
    # Only ffmpeg is a genuine PATH dependency (invoked as a subprocess). espeak-ng is BUNDLED via
    # espeakng_loader and loaded in-process by misaki, so it need not be on PATH (cycle 002).
    if shutil.which("ffmpeg") is None:
        return "ffmpeg not on PATH"
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


def test_live_two_sided_apkg(tmp_path):
    # LIVE both-sides build: a real two-sided .apkg where the Front question and the Back answer are
    # BOTH voiced, plus an empty-Front card that stays back-only.
    missing = _missing_system_deps()
    if missing:
        pytest.skip(missing)

    from ankivoice.pipeline import build_package
    from ankivoice.speech import KokoroSynthesizer

    synth = KokoroSynthesizer(voice="af_heart", lang_code="a")
    try:
        synth.synthesize("Warm up the model with a short probe.")
    except Exception as exc:  # model/voice/G2P not available offline → skip, do not fail
        pytest.skip(f"Kokoro model/voice not available: {exc}")

    job_dir = tmp_path / "job_both"
    job_dir.mkdir()
    raw = (
        b"What is the capital of France?\tThe capital of France is Paris.\n"
        b"\tThis answer has no prompt on the front.\n"  # empty Front → back-only
    )
    apkg = build_package(
        raw, synth, job_dir=job_dir, max_cards=10, deck_name="both",
        mp3_quality="4", voice_sides="both",
    )
    assert apkg.exists() and apkg.stat().st_size > 0

    with zipfile.ZipFile(apkg) as z:
        names = z.namelist()
        media = json.loads(z.read("media"))
        # card 1: front + back = 2 sounds; card 2: empty front → back-only = 1 sound; 3 distinct total
        assert len(media) == 3
        for numbered in media:  # every bundled media entry is a real, non-empty MP3
            assert len(z.read(numbered)) > 0
        bundled_basenames = set(media.values())
        db_name = next(n for n in names if n.startswith("collection.anki2"))
        data = z.read(db_name)

    dbfile = tmp_path / "both.anki2"
    dbfile.write_bytes(data)
    con = sqlite3.connect(dbfile)
    try:
        flds = [r[0].split("\x1f") for r in con.execute("SELECT flds FROM notes ORDER BY id").fetchall()]
        models = json.loads(con.execute("SELECT models FROM col").fetchone()[0])
    finally:
        con.close()

    # the 4-field both model is in use
    assert any([f["name"] for f in m["flds"]] == ["Front", "Back", "Audio", "FrontAudio"]
               for m in models.values())

    # card 1: BOTH the back sound (Audio) and the front sound (FrontAudio) are present and bundled
    front_card = next(f for f in flds if f[0] == "What is the capital of France?")
    assert front_card[2].startswith("[sound:") and front_card[2].endswith(".mp3]")  # Audio (back)
    assert front_card[3].startswith("[sound:") and front_card[3].endswith(".mp3]")  # FrontAudio (front)
    for tag in (front_card[2], front_card[3]):
        basename = tag[len("[sound:"):-1]
        assert basename in bundled_basenames  # correctly referenced → bundled media exists

    # card 2: empty Front → FrontAudio empty (back-only), back sound still present
    empty_card = next(f for f in flds if f[3] == "")
    assert empty_card[2].startswith("[sound:")
