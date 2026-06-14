"""Shared value types (data-model.md). Plain dataclasses + the job state enum."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class Card:
    """One usable card. Front MAY be empty; only a non-empty Back is required (FR-003, FR-008)."""

    front: str  # original, preserved byte-for-byte for display (FR-012)
    back: str  # original, preserved byte-for-byte for display (FR-012)
    spoken: str  # cleaned text for synthesis only; never displayed (FR-011)


@dataclass(frozen=True)
class ParsedDeck:
    cards: list[Card]
    skipped_empty_back: int  # rows skipped because Back was empty or the row had no TAB (FR-008)


class JobState(str, Enum):
    """Persisted job lifecycle (data-model.md)."""

    QUEUED = "queued"
    SYNTHESIZING = "synthesizing"
    PACKAGING = "packaging"
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
