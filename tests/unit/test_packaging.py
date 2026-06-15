"""T015 — Anki packaging (load-bearing). FR-013..016, FR-031. research.md Decision 3."""

import hashlib
import json
import shutil
import sqlite3
import tempfile
import time
import zipfile

import pytest

import genanki

from ankivoice.packaging import (
    AFMT,
    AFMT_BOTH,
    DECK_ID,
    MODEL_ID,
    MODEL_ID_BOTH,
    QFMT,
    QFMT_BOTH,
    MediaCard,
    _build_model,
    _build_model_both,
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


def test_note_field_count_mismatch_raises(tmp_path):
    # Pins the genanki field-count guard (the model has 3 fields: Front, Back, Audio). A note with the
    # wrong number of fields must raise — protecting our 3-field notes (001 tasks.md T015).
    deck = genanki.Deck(DECK_ID, "d")
    deck.add_note(genanki.Note(model=_build_model(), fields=["only-front", "only-back"]))  # 2 != 3
    with pytest.raises(Exception):
        genanki.Package(deck).write_to_file(str(tmp_path / "bad.apkg"))


def test_missing_media_file_raises(tmp_path):
    out = tmp_path / "x.apkg"
    with pytest.raises(Exception):
        build_apkg([MediaCard("F", "B", "missing.mp3")], [tmp_path / "missing.mp3"], out, deck_name="x")


def _note_and_card_counts(apkg_path, tmp_path):
    with zipfile.ZipFile(apkg_path) as z:
        db_name = next(n for n in z.namelist() if n.startswith("collection.anki2"))
        data = z.read(db_name)
    dbfile = tmp_path / "counts.anki2"
    dbfile.write_bytes(data)
    con = sqlite3.connect(dbfile)
    try:
        notes = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        cards = con.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    finally:
        con.close()
    return notes, cards


def test_empty_front_still_generates_a_studyable_card(tmp_path):
    # Regression (self-review CRITICAL): an empty Front must still produce a card (FR-003). An Anki
    # card whose question side renders empty is NOT created, so we substitute a placeholder front.
    m = _write_mp3(tmp_path / "a.mp3")
    out = tmp_path / "ef.apkg"
    build_apkg([MediaCard("", "Answer with no prompt.", "a.mp3")], [m], out, deck_name="d")
    notes, cards = _note_and_card_counts(out, tmp_path)
    assert notes == 1 and cards == 1


def test_build_apkg_leaves_no_temp_file_outside_job_dir(tmp_path, monkeypatch):
    # cycle 002 (audit B1): genanki's write_to_file uses tempfile.mkstemp() and never removes the temp
    # DB. It must land INSIDE the job dir so scoped cleanup removes it — disk stays flat (FR-024/SC-006).
    iso = tmp_path / "systmp"
    iso.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(iso))  # isolate the "system" temp dir
    job = tmp_path / "job_1"
    job.mkdir()
    m = _write_mp3(job / "a.mp3")
    build_apkg([MediaCard("F", "B", "a.mp3")], [m], job / "d.apkg", deck_name="d")
    shutil.rmtree(job)  # mirror remove_job_dir
    assert list(iso.iterdir()) == []  # NO leftover temp DB in the system temp dir


def test_build_apkg_rejects_card_audio_without_matching_media(tmp_path):
    # cycle 002 (audit E1): a card's [sound:] basename must have a bundled media file, else the card
    # would ship with silent/broken audio. build_apkg must refuse rather than produce a broken deck.
    m = _write_mp3(tmp_path / "present.mp3")
    out = tmp_path / "mismatch.apkg"
    with pytest.raises(ValueError, match="audio"):
        build_apkg([MediaCard("F", "B", "absent.mp3")], [m], out, deck_name="d")


def _read_notes_flds(apkg_path, tmp_path, fname="read.anki2"):
    """Return [[field, ...], ...] for every note, in insertion order."""
    with zipfile.ZipFile(apkg_path) as z:
        db_name = next(n for n in z.namelist() if n.startswith("collection.anki2"))
        data = z.read(db_name)
    dbfile = tmp_path / fname
    dbfile.write_bytes(data)
    con = sqlite3.connect(dbfile)
    try:
        rows = con.execute("SELECT flds FROM notes ORDER BY id").fetchall()
        models = json.loads(con.execute("SELECT models FROM col").fetchone()[0])
    finally:
        con.close()
    return [r[0].split("\x1f") for r in rows], models


# --- both-sides voicing: a second audio field + a front-audio question template ---

def test_both_model_adds_front_audio_field():
    m = _build_model_both()
    assert [f["name"] for f in m.fields] == ["Front", "Back", "Audio", "FrontAudio"]
    assert MODEL_ID_BOTH != MODEL_ID  # distinct note type so both kinds of decks coexist on import


def test_both_templates_front_audio_on_question_back_audio_on_answer():
    # question side carries the FRONT sound (auto-play on the front + replay button)
    assert "{{FrontAudio}}" in QFMT_BOTH
    assert "{{Audio}}" not in QFMT_BOTH       # the back sound is NOT on the question
    # answer side carries the BACK sound directly (auto-play on reveal + replay button)
    assert "{{Audio}}" in AFMT_BOTH
    # the front is brought in via {{FrontSide}} (NOT a direct {{FrontAudio}}), so Anki does NOT
    # auto-replay the front audio on the answer — verified against the Anki manual (no re-blast)
    assert "{{FrontSide}}" in AFMT_BOTH
    assert "{{FrontAudio}}" not in AFMT_BOTH


def test_both_mode_apkg_carries_front_and_back_sounds(tmp_path):
    mf = _write_mp3(tmp_path / "fr.mp3", b"\x01")
    mb = _write_mp3(tmp_path / "bk.mp3", b"\x02")
    me = _write_mp3(tmp_path / "eo.mp3", b"\x03")
    cards = [
        MediaCard("Question?", "Answer.", "bk.mp3", front_audio_filename="fr.mp3"),
        MediaCard("", "Lonely answer.", "eo.mp3", front_audio_filename=None),  # empty front
    ]
    out = tmp_path / "both.apkg"
    build_apkg(cards, [mf, mb, me], out, deck_name="d", voice_sides="both")

    flds, models = _read_notes_flds(out, tmp_path)
    # card 0: 4 fields — front text, back text, back sound (Audio), front sound (FrontAudio)
    assert flds[0] == ["Question?", "Answer.", "[sound:bk.mp3]", "[sound:fr.mp3]"]
    # card 1: empty Front → placeholder shown, FrontAudio empty → NO front [sound:] (back-only)
    assert flds[1] == ["(no prompt — reveal the answer)", "Lonely answer.", "[sound:eo.mp3]", ""]
    # the 4-field both model is the one used
    assert any([f["name"] for f in m["flds"]] == ["Front", "Back", "Audio", "FrontAudio"]
               for m in models.values())
    # both cards (incl. the empty-Front one) are studyable — the placeholder keeps Front non-empty
    notes, ncards = _note_and_card_counts(out, tmp_path)
    assert notes == 2 and ncards == 2


def test_both_mode_front_audio_must_be_bundled(tmp_path):
    # E1 extended to the front side: a front [sound:] with no bundled media is refused
    mb = _write_mp3(tmp_path / "bk.mp3")
    with pytest.raises(ValueError, match="audio"):
        build_apkg(
            [MediaCard("Q", "A", "bk.mp3", front_audio_filename="absent_front.mp3")],
            [mb],
            tmp_path / "x.apkg",
            deck_name="d",
            voice_sides="both",
        )


# --- regression: back-only output is byte-identical to the pre-change code ---

# sha256 of collection.anki2 for the deck below, built by the PRE-CHANGE build_apkg with time frozen
# at 1700000000.0 (the only source of non-determinism). Pins that the back path is untouched: any
# change to the back model/fields/templates/guids/note structure flips this hash.
_GOLDEN_BACK_COLLECTION_SHA256 = "d5e52aec48b9e0d429f7e530d5e169e939f4b4ba7e328762512fa3412546f7e5"


def test_back_mode_collection_byte_identical_to_pre_change(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1700000000.0)  # freeze genanki's only clock read
    media = {}
    for name in ("h1.mp3", "h2.mp3", "h3.mp3"):
        media[name] = _write_mp3(tmp_path / name, name.encode())
    cards = [
        MediaCard("greeting", "Hello there.", "h1.mp3"),
        MediaCard("ampersand", "Tom &amp; Jerry &#39;run&#39;.", "h2.mp3"),
        MediaCard("", "Answer without a prompt.", "h3.mp3"),
        MediaCard("dup", "Hello there.", "h1.mp3"),  # reuses h1 (dedupe), distinct row
    ]
    out = tmp_path / "golden.apkg"
    # default voice_sides ("back") — must reproduce today's bytes exactly
    build_apkg(cards, [media["h1.mp3"], media["h2.mp3"], media["h3.mp3"]], out, deck_name="golden")
    with zipfile.ZipFile(out) as z:
        db_name = next(n for n in z.namelist() if n.startswith("collection.anki2"))
        col = z.read(db_name)
    assert hashlib.sha256(col).hexdigest() == _GOLDEN_BACK_COLLECTION_SHA256


def test_identical_rows_stay_two_distinct_cards(tmp_path):
    # Regression (self-review): two identical export rows must not collapse into one card on import.
    m = _write_mp3(tmp_path / "a.mp3")
    out = tmp_path / "dup.apkg"
    build_apkg([MediaCard("F", "B", "a.mp3"), MediaCard("F", "B", "a.mp3")], [m], out, deck_name="d")
    notes, cards = _note_and_card_counts(out, tmp_path)
    assert notes == 2 and cards == 2
