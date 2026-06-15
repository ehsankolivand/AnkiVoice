"""Deck parsing & validation (load-bearing).

Reads a tab-separated Anki text export into usable cards. Parsing is **line by line** so input rows can
never merge: each data line is split into tab-separated fields, the first two are Front and Back
(additional columns are ignored, FR-003), and a field is unwrapped of CSV-style transport quoting
**only when it is a complete balanced quoted field** (``"…"`` with internal ``""`` un-doubled, FR-011).
This matches what a normal import shows while preserving literal quotes in hand-edited fields
byte-for-byte (FR-012) and guaranteeing one card per usable row (FR-008).

Cycle 002 fixes (see specs/002-quality-bugfix-perf/audit-notes.md): a leading/unbalanced quote no
longer swallows following rows or strips literal quotes (A1); a leading UTF-8 BOM is stripped (A2,
``utf-8-sig``); a Back that cleans to whitespace is skipped+counted (A4). HTML entities are KEPT in the
displayed text (Anki renders them with ``html:true``) and decoded only for the spoken text.
"""

from __future__ import annotations

import html
import re

from .errors import ValidationError
from .models import Card, ParsedDeck

_EXPECTED_FORMAT = (
    "Each line should be `Front<TAB>Back`, where Back is the full answer sentence. "
    "Export from Anki as 'Notes in Plain Text (.txt)'."
)

# A complete balanced RFC-4180-style quoted field: opens and closes with a double-quote, every interior
# double-quote doubled. Only such fields are transport quoting; anything else is literal user content.
_BALANCED_QUOTED = re.compile(r'^"(?:[^"]|"")*"$', re.DOTALL)


def _unwrap_balanced(field: str) -> str:
    """Strip CSV transport quoting from a field IFF it is a complete balanced quoted field.

    Genuine Anki exports escape literal quotes as ``""`` inside a wrapped field, so a wrapped field is
    always balanced and round-trips. A field that merely starts with a quote (hand-edited) is left
    exactly as-is, so its characters are preserved byte-for-byte (FR-012).
    """
    if len(field) >= 2 and _BALANCED_QUOTED.match(field):
        return field[1:-1].replace('""', '"')
    return field


def clean_for_speech(field: str) -> str:
    """Return text suitable for speech: balanced transport-quote unwrap, then HTML entities decoded.

    This is the spoken-text transform (FR-011): the same transport decoding applied to the displayed
    field, plus entity decoding so the audio sounds natural. Idempotent on already-clean text.
    """
    return html.unescape(_unwrap_balanced(field))


def parse_deck(raw: bytes, *, max_cards: int) -> ParsedDeck:
    """Parse raw upload bytes into a :class:`ParsedDeck` or raise :class:`ValidationError`.

    Rejections (friendly, actionable): ``WRONG_FORMAT`` (undecodable UTF-8, or no row has a TAB),
    ``TOO_MANY_CARDS`` (> ``max_cards``), ``EMPTY`` (TABs present but zero usable cards). Rows whose
    Back is empty / whitespace-only / cleans to nothing, and rows with no TAB, are skipped and counted
    (FR-008). Front may be empty (FR-003).
    """
    try:
        # utf-8-sig strips a leading byte-order mark (common on Windows exports) but is otherwise
        # identical to utf-8 and still raises on truly non-UTF-8 bytes (A2, FR-004).
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValidationError(
            code="WRONG_FORMAT",
            user_message=(
                "I couldn't read that file as text. Please send a UTF-8 tab-separated Anki export. "
                + _EXPECTED_FORMAT
            ),
        ) from None

    # Normalize line endings so \r\n / lone \r neither corrupt a field nor merge rows.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    # Skip the leading, contiguous block of Anki header lines (e.g. "#separator:tab"). Once a non-header
    # line is seen, later '#' lines are treated as data (FR-002).
    start = 0
    while start < len(lines) and lines[start].lstrip().startswith("#"):
        start += 1

    cards: list[Card] = []
    skipped_empty_back = 0
    saw_tab = False

    for line in lines[start:]:
        if line.strip() == "":
            continue  # blank line — not a row, not counted
        fields = line.split("\t")
        if len(fields) < 2:
            skipped_empty_back += 1  # a real line with no TAB → no Back (FR-008)
            continue
        saw_tab = True
        front = _unwrap_balanced(fields[0])  # extra columns (fields[2:]) ignored (FR-003)
        back = _unwrap_balanced(fields[1])
        if back.strip() == "":
            skipped_empty_back += 1  # empty Back → cannot be voiced (FR-008)
            continue
        spoken = clean_for_speech(fields[1])
        if spoken.strip() == "":
            skipped_empty_back += 1  # Back cleans to whitespace (e.g. "&#32;") → cannot be voiced (A4)
            continue
        cards.append(Card(front=front, back=back, spoken=spoken))

    if not saw_tab:
        raise ValidationError(
            code="WRONG_FORMAT",
            user_message="I couldn't find any tab-separated cards in that file. " + _EXPECTED_FORMAT,
        )
    if len(cards) > max_cards:
        raise ValidationError(
            code="TOO_MANY_CARDS",
            user_message=(
                f"That deck has too many cards ({len(cards)}); the limit is {max_cards}. "
                "Please split it into smaller decks and send them one at a time."
            ),
        )
    if not cards:
        raise ValidationError(
            code="EMPTY",
            user_message="That file has no usable cards — every card needs a Back (answer) sentence.",
        )

    return ParsedDeck(cards=cards, skipped_empty_back=skipped_empty_back)
