"""MP3 encoding (pure). research.md Decision 2: ffmpeg + libmp3lame, WAV piped via stdin.

Portable on a stock Linux VPS (``apt install ffmpeg``), offline, dependency-light. soundfile is used
only to build an in-memory PCM_16 WAV buffer (its own MP3 support is build-optional and unreliable on
a VPS, so we never use it to write MP3).
"""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf


def encode_mp3(
    samples: np.ndarray, sample_rate: int, out_path: Path | str, *, quality: str = "4"
) -> Path:
    """Encode mono float32 ``samples`` to an MP3 at ``out_path``. Returns the path.

    Raises ``RuntimeError`` if ffmpeg is missing or the encode fails.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError(
            "ffmpeg was not found on PATH. Install ffmpeg (with libmp3lame) to encode audio "
            "(e.g. `apt-get install ffmpeg` or `brew install ffmpeg`)."
        )

    data = np.asarray(samples, dtype=np.float32).reshape(-1)  # ensure 1-D mono float32
    buf = io.BytesIO()
    sf.write(buf, data, sample_rate, format="WAV", subtype="PCM_16")
    wav_bytes = buf.getvalue()

    out_path = Path(out_path)
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0",            # read the WAV from stdin
        "-ac", "1",                # force mono
        "-codec:a", "libmp3lame",
        "-qscale:a", str(quality),  # VBR; clear speech at small size (research.md)
        str(out_path),
    ]
    proc = subprocess.run(cmd, input=wav_bytes, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg MP3 encode failed (exit {proc.returncode}): "
            f"{proc.stderr.decode(errors='replace')}"
        )
    return out_path
