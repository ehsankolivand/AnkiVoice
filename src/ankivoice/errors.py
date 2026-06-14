"""Typed, user-facing errors.

``ValidationError`` carries a machine ``code`` and a friendly, actionable ``user_message`` that is
safe to send straight to the user (FR-004..FR-007, FR-009, SC-009).
"""

from __future__ import annotations


class ValidationError(Exception):
    """Raised when an input cannot be turned into a deck. ``user_message`` is shown to the user."""

    def __init__(self, *, code: str, user_message: str) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
