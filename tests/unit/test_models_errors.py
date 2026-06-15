"""T007 — shared value types and the typed, friendly validation error."""

import pytest

from ankivoice.errors import ValidationError
from ankivoice.models import Card, Job, JobState, ParsedDeck


def test_card_preserves_fields():
    c = Card(front="F", back="B", spoken="b")
    assert (c.front, c.back, c.spoken) == ("F", "B", "b")


def test_card_allows_empty_front():
    c = Card(front="", back="A full sentence.", spoken="A full sentence.")
    assert c.front == "" and c.back


def test_parsed_deck_counts():
    pd = ParsedDeck(cards=[Card("F", "B", "b")], skipped_empty_back=2)
    assert len(pd.cards) == 1
    assert pd.skipped_empty_back == 2


def test_jobstate_values_complete():
    # PACKAGING removed in cycle 002 (was set only AFTER packaging finished; never observable as a
    # distinct step). Synthesis+packaging are one CPU step (SYNTHESIZING); delivery is UPLOADING.
    expected = {
        "queued",
        "synthesizing",
        "uploading",
        "delivered",
        "cleaned",
        "failed",
    }
    assert {s.value for s in JobState} == expected
    assert not hasattr(JobState, "PACKAGING")
    assert JobState.QUEUED.value == "queued"
    # str-enum: comparing to the raw string works
    assert JobState.FAILED == "failed"


def test_job_dataclass_has_delivery_flags():
    j = Job(
        id=1,
        user_id=2,
        chat_id=3,
        input_path="/work/job_1/input.txt",
        original_filename="vocab.txt",
        state=JobState.QUEUED,
        error_reason=None,
        created_at="2026-06-15T00:00:00Z",
        updated_at="2026-06-15T00:00:00Z",
    )
    assert j.state is JobState.QUEUED
    assert j.original_filename == "vocab.txt"
    # cycle 002: per-copy delivery idempotency flags default False
    assert j.archive_sent is False
    assert j.user_sent is False


def test_validation_error_carries_code_and_user_message():
    e = ValidationError(code="EMPTY", user_message="That file has no usable cards.")
    assert e.code == "EMPTY"
    assert e.user_message == "That file has no usable cards."
    assert "no usable cards" in str(e)
    with pytest.raises(ValidationError):
        raise ValidationError(code="WRONG_FORMAT", user_message="bad")
