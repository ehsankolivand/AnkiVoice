"""T014 — fail-fast startup guard (cycle 002, audit C1, research D11).

A missing espeak-ng silently drops out-of-dictionary words from audio, a missing ffmpeg fails the
first encode, and an uncached voice fails the first job offline. The guard converts these into an
immediate, specific startup failure (and prewarms the model). Fully offline: the synthesizer is faked.
"""

import pytest

import ankivoice.preflight as preflight
from ankivoice.config import load_config
from ankivoice.preflight import PreflightError, check_runtime


def _config(**over):
    env = {"ANKIVOICE_BOT_TOKEN": "t", "ANKIVOICE_ARCHIVE_CHAT_ID": "1", **over}
    return load_config(env)


class _ProbeSynth:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    def synthesize(self, text):
        self.calls.append(text)
        if self.fail:
            raise RuntimeError("LocalEntryNotFoundError: voice not cached")
        import numpy as np

        return np.zeros(8, dtype=np.float32)


def _which_all_present(name):
    return f"/usr/bin/{name}"


def test_missing_ffmpeg_raises_naming_it(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda n: None if n == "ffmpeg" else f"/bin/{n}")
    with pytest.raises(PreflightError, match="ffmpeg"):
        check_runtime(_config(), _ProbeSynth())


def test_espeak_ng_not_on_path_is_fine(monkeypatch):
    # cycle 002 (self-review #0): misaki uses a BUNDLED espeak-ng (espeakng_loader), not a PATH binary.
    # The guard must NOT refuse startup just because `espeak-ng` is absent from PATH — only the probe
    # synthesis (which exercises the real phonemizer) is the ground truth.
    monkeypatch.setattr(preflight.shutil, "which", lambda n: f"/bin/{n}" if n == "ffmpeg" else None)
    synth = _ProbeSynth()  # phonemizer works
    check_runtime(_config(), synth)  # must NOT raise
    assert len(synth.calls) == 1


def test_broken_phonemizer_or_uncached_voice_raises_with_warmup_hint(monkeypatch):
    # The probe synth raising (broken bundled espeak lib OR uncached voice/model offline) → refuse start.
    monkeypatch.setattr(preflight.shutil, "which", _which_all_present)
    synth = _ProbeSynth(fail=True)
    with pytest.raises(PreflightError) as ei:
        check_runtime(_config(ANKIVOICE_DEFAULT_VOICE="am_michael"), synth)
    msg = str(ei.value)
    assert "am_michael" in msg and "warm" in msg.lower()


def test_all_present_returns_and_prewarms(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", _which_all_present)
    synth = _ProbeSynth()
    check_runtime(_config(), synth)
    assert len(synth.calls) == 1  # probed once → model prewarmed


def test_skip_preflight_env_short_circuits(monkeypatch):
    calls = {"which": 0}

    def _w(n):
        calls["which"] += 1
        return None

    monkeypatch.setattr(preflight.shutil, "which", _w)
    monkeypatch.setenv("ANKIVOICE_SKIP_PREFLIGHT", "1")
    synth = _ProbeSynth(fail=True)
    check_runtime(_config(), synth)  # must not raise, must not check
    assert calls["which"] == 0 and synth.calls == []
