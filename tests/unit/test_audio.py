"""T013 — MP3 encoding via ffmpeg/libmp3lame (load-bearing). research.md Decision 2."""

import shutil
import subprocess

import numpy as np
import pytest

import ankivoice.audio as audio
from ankivoice.audio import encode_mp3


def _tone(seconds: float = 0.3, sr: int = 24000) -> np.ndarray:
    t = np.arange(int(sr * seconds), dtype=np.float32)
    return (0.2 * np.sin(2 * np.pi * 440.0 * t / sr)).astype(np.float32)


def test_encode_mp3_writes_valid_mono_mp3(tmp_path):
    out = tmp_path / "clip.mp3"
    result = encode_mp3(_tone(), 24000, out, quality="4")
    assert result == out
    assert out.exists() and out.stat().st_size > 0

    head = out.read_bytes()[:3]
    assert head == b"ID3" or head[0] == 0xFF  # ID3 tag or MPEG frame sync

    if shutil.which("ffprobe"):
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_name,channels",
                "-of", "default=nw=1",
                str(out),
            ],
            capture_output=True,
            text=True,
        )
        assert "codec_name=mp3" in probe.stdout
        assert "channels=1" in probe.stdout


def test_encode_mp3_accepts_2d_mono_and_flattens(tmp_path):
    out = tmp_path / "c2.mp3"
    encode_mp3(_tone().reshape(-1, 1), 24000, out, quality="4")
    assert out.exists() and out.stat().st_size > 0


def test_encode_mp3_raises_clearly_when_ffmpeg_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="ffmpeg"):
        encode_mp3(_tone(), 24000, tmp_path / "x.mp3", quality="4")
