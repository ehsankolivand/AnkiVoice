"""T017 — core conversion pipeline (load-bearing). Dedupe, fidelity, media count."""

import json
import zipfile

import pytest

from ankivoice.errors import ValidationError
from ankivoice.pipeline import build_package


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


def test_validation_error_propagates(work_dir, fake_synth):
    job_dir = work_dir / "job_4"
    job_dir.mkdir()
    with pytest.raises(ValidationError):
        build_package(b"a\t\nb\t\n", fake_synth, job_dir=job_dir, max_cards=10, deck_name="d",
                      mp3_quality="4")
    assert fake_synth.calls == []  # nothing synthesized for an invalid deck
