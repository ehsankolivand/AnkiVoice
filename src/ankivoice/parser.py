"""Deck parsing & validation (load-bearing).

Reads a tab-separated Anki text export into usable cards. Field extraction uses the stdlib ``csv``
reader (delimiter = TAB), which correctly removes CSV-style surrounding quotes and collapses doubled
quotes (``""`` → ``"``) — so the spoken AND displayed text both have transport quoting removed
(FR-011). HTML entities are KEPT in the displayed text (Anki renders them with ``html:true``) and
decoded only for the spoken text via :func:`clean_for_speech`. Original card text is otherwise
preserved exactly (FR-012).
"""

from __future__ import annotations

import csv
import html
import io

from .errors import ValidationError
from .models import Card, ParsedDeck

_EXPECTED_FORMAT = (
    "Each line should be `Front<TAB>Back`, where Back is the full answer sentence. "
    "Export from Anki as 'Notes in Plain Text (.txt)'."
)


def clean_for_speech(field: str) -> str:
    """Return text suitable for speech synthesis: HTML entities decoded (FR-011).

    CSV quote-unwrapping is handled structurally by the parser's csv reader, so this function only
    needs to decode entities. It is intentionally idempotent on already-clean text.
    """
    return html.unescape(field)


def parse_deck(raw: bytes, *, max_cards: int) -> ParsedDeck:
    """Parse raw upload bytes into a :class:`ParsedDeck` or raise :class:`ValidationError`.

    Rejections (friendly, actionable): ``WRONG_FORMAT`` (undecodable UTF-8, or no row has a TAB),
    ``TOO_MANY_CARDS`` (> ``max_cards``), ``EMPTY`` (TABs present but zero usable cards). Rows whose
    Back is empty, or rows with no TAB, are skipped and counted (FR-008). Front may be empty (FR-003).
    """
    try:
        text = raw.decode("utf-8")
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

    # Skip the leading, contiguous block of Anki header lines (e.g. "#separator:tab"), preserving the
    # rest verbatim. Once a non-header line is seen, later '#' lines are treated as data (FR-002).
    lines = text.splitlines(keepends=True)
    start = 0
    while start < len(lines) and lines[start].lstrip().startswith("#"):
        start += 1
    body = "".join(lines[start:])

    cards: list[Card] = []
    skipped_empty_back = 0
    saw_tab = False

    # csv.reader over a StringIO (a file-like) so multiline quoted fields parse correctly; a malformed
    # file (e.g. an unterminated quote) raises csv.Error → friendly WRONG_FORMAT rather than a crash.
    try:
        for row in csv.reader(io.StringIO(body), delimiter="\t", quotechar='"'):
            if not row or (len(row) == 1 and row[0].strip() == ""):
                continue  # blank line — not a row, not counted
            if len(row) < 2:
                skipped_empty_back += 1  # a real line with no TAB → no Back (FR-008)
                continue
            saw_tab = True
            front, back = row[0], row[1]  # extra columns (row[2:]) ignored (FR-003)
            if back.strip() == "":
                skipped_empty_back += 1  # empty Back → cannot be voiced (FR-008)
                continue
            cards.append(Card(front=front, back=back, spoken=clean_for_speech(back)))
    except csv.Error:
        raise ValidationError(
            code="WRONG_FORMAT",
            user_message="I couldn't parse that file — it looks malformed. " + _EXPECTED_FORMAT,
        ) from None

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
