"""Shared value types (data-model.md). Plain dataclasses + the job state enum."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class Card:
    """One usable card. Front MAY be empty; only a non-empty Back is required (FR-003, FR-008)."""

    front: str  # original, preserved byte-for-byte for display (FR-012)
    back: str  # original, preserved byte-for-byte for display (FR-012)
    spoken: str  # cleaned Back text for synthesis only; never displayed (FR-011)
    # Cleaned Front text for synthesis (same clean_for_speech rule as ``spoken``), used only when
    # voicing BOTH sides. Empty/whitespace ⇒ the card has no Front audio (the empty-Front placeholder
    # is never voiced). Defaults to "" so back-only construction (and existing call sites) are unchanged.
    front_spoken: str = ""


@dataclass(frozen=True)
class ParsedDeck:
    cards: list[Card]
    skipped_empty_back: int  # rows skipped because Back was empty or the row had no TAB (FR-008)


class JobState(str, Enum):
    """Persisted job lifecycle (data-model.md).

    Cycle 002: ``PACKAGING`` was removed — synthesis and packaging run inside one CPU step
    (``SYNTHESIZING``); the worker moves a job straight to ``UPLOADING`` the instant the build returns
    (which is what preserves "at most one SYNTHESIZING"). PACKAGING was only ever set *after* packaging
    had already finished, so it was a misnomer that no logic meaningfully observed.
    """

    QUEUED = "queued"
    SYNTHESIZING = "synthesizing"
    UPLOADING = "uploading"
    DELIVERED = "delivered"
    CLEANED = "cleaned"  # terminal (success)
    FAILED = "failed"  # terminal (processing failure)


@dataclass
class Job:
    id: int
    user_id: int
    chat_id: int
    input_path: str
    original_filename: str | None
    state: JobState
    error_reason: str | None
    created_at: str
    updated_at: str
    # Cycle 002: per-copy delivery idempotency flags so a mid-delivery crash re-sends only the missing
    # copy and a fully-delivered job is never re-sent (data-model.md, research D8).
    archive_sent: bool = False
    user_sent: bool = False
