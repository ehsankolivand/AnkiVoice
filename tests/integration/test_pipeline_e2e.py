"""T019 — END-TO-END (Constitution Principle VII): sample deck in → importable .apkg with playable
audio out. Uses the FakeSynthesizer so the default suite is fast and fully offline."""

import json
import sqlite3
import zipfile

from ankivoice.pipeline import build_package


def test_sample_deck_to_importable_apkg(work_dir, fake_synth, sample_deck_bytes):
    job_dir = work_dir / "job_e2e"
    job_dir.mkdir()

    apkg = build_package(
        sample_deck_bytes, fake_synth, job_dir=job_dir, max_cards=200, deck_name="vocab",
        mp3_quality="4",
    )
    assert apkg.name == "vocab.apkg"
    assert apkg.exists()

    with zipfile.ZipFile(apkg) as z:
        names = z.namelist()
        db_name = next(n for n in names if n.startswith("collection.anki2"))  # importable collection
        assert "media" in names
        media = json.loads(z.read("media"))
        assert len(media) == 6  # one audio per usable card (6 usable, all distinct)
        assert all(v.endswith(".mp3") for v in media.values())
        db_bytes = z.read(db_name)

    dbfile = job_dir / "collection_check.anki2"
    dbfile.write_bytes(db_bytes)
    con = sqlite3.connect(dbfile)
    try:
        notes = [row[0] for row in con.execute("SELECT flds FROM notes")]
        card_count = con.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    finally:
        con.close()

    assert len(notes) == 6  # skipped (empty-Back, no-TAB) rows excluded
    # every usable card is a STUDYABLE card (incl. the empty-Front one) — not just a note
    assert card_count == 6
    assert all("[sound:" in flds for flds in notes)  # every card auto-plays on reveal

    joined = "\n".join(notes)
    # original text preserved exactly (entities kept; CSV transport quotes removed)
    assert "Tom &amp; Jerry &#39;run&#39; very fast." in joined
    assert 'She said, "Hello there!" to me.' in joined
    # the empty-Front card made it in (its Back is present)
    assert "This sentence has no prompt on the front." in joined
