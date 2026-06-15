"""T009 — deck parsing/validation (load-bearing). FR-002..008, FR-011, FR-012."""

import pytest

from ankivoice.errors import ValidationError
from ankivoice.parser import clean_for_speech, parse_deck


# --- clean_for_speech: HTML entities decoded for spoken text (FR-011) ---

def test_clean_decodes_html_entities():
    assert clean_for_speech("Tom &amp; Jerry") == "Tom & Jerry"
    assert clean_for_speech("&#39;quoted&#39;") == "'quoted'"
    assert clean_for_speech("plain text") == "plain text"


def test_clean_for_speech_unwraps_balanced_then_unescapes():
    # cycle 002: clean_for_speech now does balanced-transport-unwrap THEN html.unescape (matches contract)
    assert clean_for_speech('"He said ""hi"" &amp; left."') == 'He said "hi" & left.'
    # a NON-balanced leading quote is NOT unwrapped (literal content)
    assert clean_for_speech('"Break a leg" means good luck.') == '"Break a leg" means good luck.'


# --- cycle 002 regression: CSV reader misuse must not swallow rows or strip literal quotes (audit A1) ---

def test_unbalanced_leading_quote_does_not_swallow_following_rows():
    raw = b'q1\t"This quote is unbalanced.\nq2\tThis row gets swallowed.\nq3\tAnd this one too.\n'
    deck = parse_deck(raw, max_cards=50)
    assert len(deck.cards) == 3  # was 1 before the fix (rows merged)
    assert deck.cards[0].back == '"This quote is unbalanced.'  # literal quote preserved (FR-012)
    assert deck.cards[1].back == "This row gets swallowed."
    assert deck.cards[2].back == "And this one too."


def test_literal_leading_quote_preserved_in_display():
    deck = parse_deck(b'idiom\t"Break a leg" means good luck.\n', max_cards=10)
    assert deck.cards[0].back == '"Break a leg" means good luck.'  # not a balanced field → verbatim
    # spoken decodes entities but the literal quotes remain (not transport quoting)
    assert deck.cards[0].spoken == '"Break a leg" means good luck.'


def test_utf8_bom_does_not_leak_a_header_card(sample_deck_bytes):
    # A Windows export saved with a BOM must still skip the #header block (audit A2).
    raw = "﻿#separator:tab\n#html:true\n#columns:Front\tBack\ngreeting\tHello there.\n".encode("utf-8")
    deck = parse_deck(raw, max_cards=10)
    assert len(deck.cards) == 1
    assert deck.cards[0].front == "greeting" and deck.cards[0].back == "Hello there."
    assert all(not c.front.startswith("#") for c in deck.cards)


def test_back_that_cleans_to_whitespace_is_skipped(sample_deck_bytes):
    # A Back of only an encoded space cannot be voiced → skipped + counted (audit A4).
    deck = parse_deck(b"a\t&#32;\nb\tReal answer here.\n", max_cards=10)
    assert [c.back for c in deck.cards] == ["Real answer here."]
    assert deck.skipped_empty_back == 1


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


def test_crlf_line_endings_parse_cleanly():
    # Regression (self-review): \r\n must not leave a trailing \r in fields or merge rows.
    deck = parse_deck(b"f1\tSentence one.\r\nf2\tSentence two.\r\n", max_cards=10)
    assert len(deck.cards) == 2
    assert deck.cards[0].back == "Sentence one."
    assert deck.cards[1].back == "Sentence two."


def test_lone_cr_line_endings_parse_cleanly():
    deck = parse_deck(b"f1\tone.\rf2\ttwo.\r", max_cards=10)
    assert [c.back for c in deck.cards] == ["one.", "two."]


def test_no_tab_row_skipped_and_counted():
    raw = b"good\tsentence one\nnotabhere\nalso good\tsentence two\n"
    deck = parse_deck(raw, max_cards=10)
    assert len(deck.cards) == 2
    assert deck.skipped_empty_back == 1
