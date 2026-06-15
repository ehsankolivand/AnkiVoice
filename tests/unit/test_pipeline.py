"""T017 — core conversion pipeline (load-bearing). Dedupe, fidelity, media count."""

import hashlib
import json
import zipfile

import pytest

from ankivoice.errors import ValidationError
from ankivoice.pipeline import build_package


def test_mp3_filename_uses_full_sha256_digest(work_dir, fake_synth):
    # cycle 002 (audit A3): a 16-hex (64-bit) prefix could collide across distinct sentences and
    # overwrite audio. The filename must use the FULL hexdigest of the spoken text.
    raw = b"a\tThe one and only sentence.\n"
    job_dir = work_dir / "job_fd"
    job_dir.mkdir()
    build_package(raw, fake_synth, job_dir=job_dir, max_cards=10, deck_name="d", mp3_quality="4")
    expected = hashlib.sha256("The one and only sentence.".encode("utf-8")).hexdigest() + ".mp3"
    names = {p.name for p in job_dir.glob("*.mp3")}
    assert names == {expected}
    assert len(expected) == 64 + 4  # full digest, not truncated


def test_dedupes_identical_spoken_synthesizes_once(work_dir, fake_synth):
    raw = b"a\tHello there.\nb\tHello there.\nc\tA different sentence.\n"
    job_dir = work_dir / "job_1"
    job_dir.mkdir()
    apkg = build_package(
        raw, fake_synth, job_dir=job_dir, max_cards=10, deck_name="d", mp3_quality="4"
    )
    # 3 usable cards but only 2 distinct spoken strings → synthesize twice (Constitution P1)
    assert len(fake_synth.calls) == 2
    assert apkg.exists()
    assert len(list(job_dir.glob("*.mp3"))) == 2
    with zipfile.ZipFile(apkg) as z:
        assert len(json.loads(z.read("media"))) == 2


def test_uses_cleaned_text_for_audio(work_dir, fake_synth):
    raw = b"q\tTom &amp; Jerry run.\n"
    job_dir = work_dir / "job_2"
    job_dir.mkdir()
    build_package(raw, fake_synth, job_dir=job_dir, max_cards=10, deck_name="d", mp3_quality="4")
    assert fake_synth.calls == ["Tom & Jerry run."]  # entities decoded for the audio


def test_media_count_equals_usable_cards(work_dir, fake_synth, sample_deck_bytes):
    job_dir = work_dir / "job_3"
    job_dir.mkdir()
    apkg = build_package(
        sample_deck_bytes, fake_synth, job_dir=job_dir, max_cards=200, deck_name="vocab",
        mp3_quality="4",
    )
    assert apkg.name == "vocab.apkg"
    with zipfile.ZipFile(apkg) as z:
        assert len(json.loads(z.read("media"))) == 6  # 6 usable cards, all unique sentences


# --- both-sides voicing (ANKIVOICE_VOICE_SIDES=both) ---

def test_back_mode_default_voices_back_only(work_dir, fake_synth):
    # build_package's builder default (no voice_sides arg) is "back" — voices ONLY the Back. (The
    # PRODUCT default is "both" via config; the worker always passes config.voice_sides explicitly.)
    raw = b"Question one?\tAnswer one.\n"
    job_dir = work_dir / "job_back"
    job_dir.mkdir()
    build_package(raw, fake_synth, job_dir=job_dir, max_cards=10, deck_name="d", mp3_quality="4")
    assert fake_synth.calls == ["Answer one."]
    assert len(list(job_dir.glob("*.mp3"))) == 1


def test_both_mode_voices_front_and_back(work_dir, fake_synth):
    raw = b"Question one?\tAnswer one.\n"
    job_dir = work_dir / "job_both"
    job_dir.mkdir()
    build_package(
        raw, fake_synth, job_dir=job_dir, max_cards=10, deck_name="d",
        mp3_quality="4", voice_sides="both",
    )
    # front + back are distinct strings → two syntheses, two MP3s
    assert sorted(fake_synth.calls) == ["Answer one.", "Question one?"]
    assert len(list(job_dir.glob("*.mp3"))) == 2


def test_both_mode_cross_side_dedupe_synthesizes_once(work_dir, fake_synth):
    # identical cleaned text appearing on a Front AND a Back (anywhere in the deck) synthesizes once
    raw = b"Hello there.\tSomething else.\nx\tHello there.\n"
    job_dir = work_dir / "job_cross"
    job_dir.mkdir()
    build_package(
        raw, fake_synth, job_dir=job_dir, max_cards=10, deck_name="d",
        mp3_quality="4", voice_sides="both",
    )
    # distinct spoken strings: "Hello there." (front of c1 AND back of c2), "Something else.", "x"
    assert sorted(fake_synth.calls) == ["Hello there.", "Something else.", "x"]
    assert len(fake_synth.calls) == len(set(fake_synth.calls))  # each synthesized exactly once
    assert len(list(job_dir.glob("*.mp3"))) == 3


def test_both_mode_empty_front_is_back_only(work_dir, fake_synth):
    # an empty Front yields NO Front audio even in both mode
    raw = b"\tThe only answer.\n"
    job_dir = work_dir / "job_ef"
    job_dir.mkdir()
    build_package(
        raw, fake_synth, job_dir=job_dir, max_cards=10, deck_name="d",
        mp3_quality="4", voice_sides="both",
    )
    assert fake_synth.calls == ["The only answer."]
    assert len(list(job_dir.glob("*.mp3"))) == 1


def test_validation_error_propagates(work_dir, fake_synth):
    job_dir = work_dir / "job_4"
    job_dir.mkdir()
    with pytest.raises(ValidationError):
        build_package(b"a\t\nb\t\n", fake_synth, job_dir=job_dir, max_cards=10, deck_name="d",
                      mp3_quality="4")
    assert fake_synth.calls == []  # nothing synthesized for an invalid deck
