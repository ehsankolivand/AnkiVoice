"""Speech synthesis wrapper (load-bearing).

Wraps Kokoro-82M for local, CPU-only, offline English synthesis (research.md). The real model load
lives in :func:`_load_pipeline` (lazy torch/kokoro import + CPU thread pinning) so it can be mocked in
the fast offline test suite; real CPU execution is exercised by the live test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

# Kokoro outputs mono float32 at a fixed 24 kHz (research.md; no public constant).
SAMPLE_RATE = 24000
DEFAULT_REPO_ID = "hexgrad/Kokoro-82M"


@runtime_checkable
class Synthesizer(Protocol):
    """The minimal speech interface the pipeline depends on. Faked in tests."""

    sample_rate: int

    def synthesize(self, spoken_text: str) -> np.ndarray:  # mono float32 PCM
        ...


def _load_pipeline(*, lang_code: str, repo_id: str, device: str, model_dir: Path | None):
    """Construct a Kokoro pipeline. Heavy + imports torch — patched out in the default test suite."""
    import os

    import torch
    from kokoro import KPipeline

    torch.set_num_threads(1)  # respect the single-core budget (Constitution P1)
    if model_dir is not None:
        os.environ.setdefault("HF_HOME", str(model_dir))  # pin the offline model cache
    return KPipeline(lang_code=lang_code, repo_id=repo_id, device=device)


class KokoroSynthesizer:
    """Local, offline, CPU-only Kokoro synthesizer. Loads the model once and reuses it (P1, P4)."""

    def __init__(
        self,
        *,
        voice: str,
        lang_code: str,
        model_dir: Path | None = None,
        repo_id: str = DEFAULT_REPO_ID,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        self.voice = voice
        self.lang_code = lang_code
        self.model_dir = model_dir
        self.repo_id = repo_id
        self.sample_rate = sample_rate
        self._pipeline = None

    def _ensure_pipeline(self):
        if self._pipeline is None:
            self._pipeline = _load_pipeline(
                lang_code=self.lang_code,
                repo_id=self.repo_id,
                device="cpu",
                model_dir=self.model_dir,
            )
        return self._pipeline

    def synthesize(self, spoken_text: str) -> np.ndarray:
        """Synthesize one sentence to a 1-D mono float32 array (concatenating Kokoro's chunks)."""
        pipeline = self._ensure_pipeline()
        chunks: list[np.ndarray] = []
        for result in pipeline(spoken_text, voice=self.voice, speed=1.0):
            audio = result.output.audio
            chunks.append(np.asarray(audio.detach().cpu().numpy(), dtype=np.float32))
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        data = chunks[0] if len(chunks) == 1 else np.concatenate(chunks)
        return np.asarray(data, dtype=np.float32).reshape(-1)
