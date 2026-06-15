"""T011 — speech wrapper (load-bearing). Kokoro is mocked: no model, no torch import, offline."""

import numpy as np

import ankivoice.speech as speech


class _FakeTensor:
    def __init__(self, arr):
        self._a = np.asarray(arr, dtype=np.float32)

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self._a


class _FakeResult:
    def __init__(self, arr):
        self.output = type("Out", (), {"audio": _FakeTensor(arr)})()


def _make_fake_pipeline(chunks):
    calls: list[tuple] = []

    def pipeline(text, voice=None, speed=1.0):
        calls.append((text, voice, speed))
        return iter([_FakeResult(c) for c in chunks])

    pipeline.calls = calls
    return pipeline


def test_loads_pipeline_once_and_concatenates_generator(mocker):
    fake_pipeline = _make_fake_pipeline([[0.1, 0.2], [0.3]])
    load = mocker.patch("ankivoice.speech._load_pipeline", return_value=fake_pipeline)

    synth = speech.KokoroSynthesizer(voice="af_heart", lang_code="a")
    out = synth.synthesize("hello world")

    assert out.dtype == np.float32
    assert out.ndim == 1
    np.testing.assert_allclose(out, [0.1, 0.2, 0.3], rtol=1e-6)
    assert synth.sample_rate == 24000

    # built on CPU with explicit repo_id (silences kokoro's default warning)
    load.assert_called_once_with(
        lang_code="a", repo_id="hexgrad/Kokoro-82M", device="cpu", model_dir=None
    )
    assert fake_pipeline.calls[0] == ("hello world", "af_heart", 1.0)

    # second synthesis reuses the same loaded pipeline (load ONCE)
    synth.synthesize("again")
    load.assert_called_once()
    assert len(fake_pipeline.calls) == 2


def test_empty_text_returns_empty_float32(mocker):
    mocker.patch("ankivoice.speech._load_pipeline", return_value=_make_fake_pipeline([]))
    synth = speech.KokoroSynthesizer(voice="af_heart", lang_code="a")
    out = synth.synthesize("")
    assert out.dtype == np.float32 and out.shape == (0,)


def test_synthesize_runs_under_inference_mode(mocker):
    # cycle 002 (audit #6/perf): synthesis runs inside torch.inference_mode() — byte-identical output,
    # less per-sentence autograd/version bookkeeping.
    import torch

    seen = {}

    def fake_pipeline(text, voice=None, speed=1.0):
        seen["inference"] = torch.is_inference_mode_enabled()
        return iter([_FakeResult([0.1, 0.2])])

    mocker.patch("ankivoice.speech._load_pipeline", return_value=fake_pipeline)
    synth = speech.KokoroSynthesizer(voice="af_heart", lang_code="a")
    out = synth.synthesize("hello")
    assert seen["inference"] is True
    assert out.dtype == np.float32 and out.ndim == 1  # output shape/dtype unchanged


def test_satisfies_synthesizer_protocol():
    synth = speech.KokoroSynthesizer(voice="af_heart", lang_code="a")
    assert isinstance(synth.sample_rate, int)
    assert callable(synth.synthesize)
