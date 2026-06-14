"""T015 — Anki packaging (load-bearing). FR-013..016, FR-031. research.md Decision 3."""

import json
import sqlite3
import zipfile

import pytest

from ankivoice.packaging import (
    AFMT,
    DECK_ID,
    MODEL_ID,
    QFMT,
    MediaCard,
    build_apkg,
    output_name,
)


def _write_mp3(path, tag: bytes = b""):
    path.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00" + tag)
    return path


def test_audio_field_in_answer_template_only():
    # auto-play on reveal + replay button come from [sound:] on the ANSWER side only (FR-010,014,015)
    assert "{{Audio}}" in AFMT
    assert "{{Audio}}" not in QFMT


def test_ids_are_deterministic_constants():
    assert isinstance(MODEL_ID, int)
    assert isinstance(DECK_ID, int)


def test_output_name_from_stem_with_fallback():
    assert output_name("vocab.txt") == "vocab"
    assert output_name("My Deck.txt") == "My Deck"
    assert output_name("/some/path/lesson3.txt") == "lesson3"
    assert output_name(None) == "AnkiVoice deck"
    assert output_name("") == "AnkiVoice deck"
    assert output_name("   ") == "AnkiVoice deck"


def test_build_apkg_archive_structure(tmp_path):
    m1 = _write_mp3(tmp_path / "c1.mp3", b"\x01")
    m2 = _write_mp3(tmp_path / "c2.mp3", b"\x02")
    cards = [MediaCard("F1", "B1", "c1.mp3"), MediaCard("F2", "B2", "c2.mp3")]
    out = tmp_path / "deck.apkg"

    result = build_apkg(cards, [m1, m2], out, deck_name="vocab")
    assert result == out and out.exists()

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert any(n.startswith("collection.anki2") for n in names)  # the sqlite collection
        assert "media" in names
        media_map = json.loads(z.read("media"))
        assert set(media_map.values()) == {"c1.mp3", "c2.mp3"}  # bare basenames
        for numbered in media_map:  # numbered media files are bundled
            assert numbered in names


def test_notes_contain_sound_tag_and_preserve_original_text(tmp_path):
    m1 = _write_mp3(tmp_path / "a.mp3")
    out = tmp_path / "d.apkg"
    # original text contains HTML entities — must be preserved verbatim for display (FR-012)
    build_apkg(
        [MediaCard("prompt", "Tom &amp; Jerry &#39;run&#39;.", "a.mp3")],
        [m1],
        out,
        deck_name="d",
    )
    with zipfile.ZipFile(out) as z:
        db_name = next(n for n in z.namelist() if n.startswith("collection.anki2"))
        data = z.read(db_name)
    dbfile = tmp_path / "c.anki2"
    dbfile.write_bytes(data)
    con = sqlite3.connect(dbfile)
    try:
        flds = con.execute("SELECT flds FROM notes").fetchone()[0]
    finally:
        con.close()
    assert "[sound:a.mp3]" in flds  # bare filename inside the sound tag
    assert "Tom &amp; Jerry &#39;run&#39;." in flds  # entities preserved exactly


def test_missing_media_file_raises(tmp_path):
    out = tmp_path / "x.apkg"
    with pytest.raises(Exception):
        build_apkg([MediaCard("F", "B", "missing.mp3")], [tmp_path / "missing.mp3"], out, deck_name="x")
