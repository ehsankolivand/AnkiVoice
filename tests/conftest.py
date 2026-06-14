"""Shared pytest fixtures.

The default test suite is fast and FULLY OFFLINE (Constitution Principle VII): the Kokoro model is
replaced by ``FakeSynthesizer`` and the Telegram network by ``FakeSender``. No fixture here imports
torch/kokoro or touches the network.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class FakeSynthesizer:
    """Deterministic stand-in for the real Kokoro synthesizer.

    Implements the ``speech.Synthesizer`` protocol: a ``sample_rate`` attribute and
    ``synthesize(spoken_text) -> np.ndarray`` returning mono float32 PCM. Records every call so tests
    can assert the per-deck dedupe cache (identical sentences synthesize once).
    """

    def __init__(self, sample_rate: int = 24000) -> None:
        self.sample_rate = sample_rate
        self.calls: list[str] = []

    def synthesize(self, spoken_text: str) -> np.ndarray:
        self.calls.append(spoken_text)
        # Deterministic, short tone whose pitch depends on the text (audible, valid, tiny).
        seed = int(hashlib.sha256(spoken_text.encode("utf-8")).hexdigest(), 16) % 400
        n = max(2400, len(spoken_text) * 240)  # >= 0.1 s at 24 kHz
        t = np.arange(n, dtype=np.float32)
        freq = 110.0 + seed
        return (0.2 * np.sin(2 * np.pi * freq * t / self.sample_rate)).astype(np.float32)


class FakeSender:
    """Records Telegram sends in order; can be told to fail a given chat to simulate upload failure.

    Implements the ``delivery.Sender`` protocol.
    """

    def __init__(self, fail_on_chat: int | None = None) -> None:
        self.events: list[tuple] = []  # ("document", chat_id, path, filename) | ("message", chat_id, text)
        self.fail_on_chat = fail_on_chat

    async def send_document(self, chat_id: int, path, *, filename: str, caption: str | None = None) -> None:
        if self.fail_on_chat is not None and chat_id == self.fail_on_chat:
            raise RuntimeError(f"simulated send_document failure to chat {chat_id}")
        self.events.append(("document", chat_id, str(path), filename))

    async def send_message(self, chat_id: int, text: str) -> None:
        self.events.append(("message", chat_id, text))

    # convenience views for assertions
    @property
    def documents(self) -> list[tuple]:
        return [e for e in self.events if e[0] == "document"]

    @property
    def messages(self) -> list[tuple]:
        return [e for e in self.events if e[0] == "message"]


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    d.mkdir()
    return d


@pytest.fixture
def fake_synth() -> FakeSynthesizer:
    return FakeSynthesizer()


@pytest.fixture
def fake_sender() -> FakeSender:
    return FakeSender()


@pytest.fixture
def sample_deck_path() -> Path:
    return FIXTURES / "sample_deck.txt"


@pytest.fixture
def sample_deck_bytes(sample_deck_path: Path) -> bytes:
    return sample_deck_path.read_bytes()
