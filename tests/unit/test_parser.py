"""T009 — deck parsing/validation (load-bearing). FR-002..008, FR-011, FR-012."""

import pytest

from ankivoice.errors import ValidationError
from ankivoice.parser import clean_for_speech, parse_deck


# --- clean_for_speech: HTML entities decoded for spoken text (FR-011) ---

def test_clean_decodes_html_entities():
    assert clean_for_speech("Tom &amp; Jerry") == "Tom & Jerry"
    assert clean_for_speech("&#39;quoted&#39;") == "'quoted'"
    assert clean_for_speech("plain text") == "plain text"


# --- parse_deck happy path against the realistic fixture ---

def _by_front(deck, front):
    return next(c for c in deck.cards if c.front == front)


def test_parse_fixture_counts_and_skips(sample_deck_bytes):
    deck = parse_deck(sample_deck_bytes, max_cards=200)
    # 6 usable cards: greeting, thanks, ampersand, quote, (empty-front), weather
    assert len(deck.cards) == 6
    # skipped: the empty-Back row AND the no-TAB row
    assert deck.skipped_empty_back == 2


def test_headers_are_skipped(sample_deck_bytes):
    deck = parse_deck(sample_deck_bytes, max_cards=200)
    assert all(not c.front.startswith("#") for c in deck.cards)
    assert all("separator" not in c.front for c in deck.cards)


def test_original_text_preserved_for_display_entities_kept(sample_deck_bytes):
    deck = parse_deck(sample_deck_bytes, max_cards=200)
    amp = _by_front(deck, "ampersand")
    # display keeps the original entities exactly (Anki renders them with html:true)
    assert amp.back == "Tom &amp; Jerry &#39;run&#39; very fast."
    # spoken text has entities decoded so the audio sounds natural
    assert amp.spoken == "Tom & Jerry 'run' very fast."


def test_csv_quote_wrapping_unwrapped_for_both(sample_deck_bytes):
    deck = parse_deck(sample_deck_bytes, max_cards=200)
    q = _by_front(deck, "quote")
    # the surrounding CSV quotes are transport, removed; the inner doubled quotes collapse to one
    assert q.back == 'She said, "Hello there!" to me.'
    assert q.spoken == 'She said, "Hello there!" to me.'


def test_empty_front_is_usable(sample_deck_bytes):
    deck = parse_deck(sample_deck_bytes, max_cards=200)
    empties = [c for c in deck.cards if c.front == ""]
    assert len(empties) == 1
    assert empties[0].back == "This sentence has no prompt on the front."


def test_extra_columns_ignored():
    raw = b"front\tback sentence\ttag1\ttag2\n"
    deck = parse_deck(raw, max_cards=10)
    assert len(deck.cards) == 1
    assert deck.cards[0].front == "front"
    assert deck.cards[0].back == "back sentence"


def test_first_field_is_front_second_is_back():
    raw = b"Q\tThe answer.\n"
    deck = parse_deck(raw, max_cards=10)
    assert deck.cards[0].front == "Q"
    assert deck.cards[0].back == "The answer."


# --- rejection modes (FR-004..007) ---

def test_wrong_format_when_no_tab_anywhere():
    with pytest.raises(ValidationError) as ei:
        parse_deck(b"just one column\nanother line\n", max_cards=10)
    assert ei.value.code == "WRONG_FORMAT"


def test_wrong_format_when_not_utf8():
    with pytest.raises(ValidationError) as ei:
        parse_deck(b"\xff\xfe\x00\x01 not utf8 \xff", max_cards=10)
    assert ei.value.code == "WRONG_FORMAT"


def test_empty_when_zero_usable_cards_but_tabs_present():
    # tabs exist but every Back is empty -> EMPTY (not WRONG_FORMAT)
    with pytest.raises(ValidationError) as ei:
        parse_deck(b"a\t\nb\t\n", max_cards=10)
    assert ei.value.code == "EMPTY"


def test_too_many_cards():
    raw = b"f1\tb1\nf2\tb2\nf3\tb3\n"
    with pytest.raises(ValidationError) as ei:
        parse_deck(raw, max_cards=2)
    assert ei.value.code == "TOO_MANY_CARDS"


def test_no_tab_row_skipped_and_counted():
    raw = b"good\tsentence one\nnotabhere\nalso good\tsentence two\n"
    deck = parse_deck(raw, max_cards=10)
    assert len(deck.cards) == 2
    assert deck.skipped_empty_back == 1
