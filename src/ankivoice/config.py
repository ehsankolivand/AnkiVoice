"""Configuration — environment only (Constitution Principle VIII).

All settings come from ``ANKIVOICE_*`` environment variables (or a passed mapping). No secrets are
hard-coded. When no mapping is given, a local ``.env`` is loaded first as a convenience, but the
environment remains authoritative. See ``.env.example`` for every key.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# Fixed, non-configurable facts (research.md): Kokoro outputs 24 kHz; ffmpeg VBR quality for speech.
_SAMPLE_RATE = 24000
_REQUIRED = ("ANKIVOICE_BOT_TOKEN", "ANKIVOICE_ARCHIVE_CHAT_ID")


class ConfigError(Exception):
    """Raised when required configuration is missing or malformed."""


@dataclass(frozen=True)
class Config:
    bot_token: str
    archive_chat_id: int
    default_voice: str
    lang_code: str
    max_cards: int
    max_file_bytes: int
    work_dir: Path
    db_path: Path
    model_dir: Path | None
    sample_rate: int
    mp3_quality: str
    # Cycle 002: bounded operational limits (safe defaults; operator-overridable).
    # Defaults here are a convenience for direct construction (e.g. tests); load_config always sets
    # them explicitly from the environment, so production configuration stays fully explicit.
    job_history: int = 500        # max retained terminal job rows (datastore bound)
    ffmpeg_timeout: int = 120     # seconds before an MP3 encode is aborted
    delivery_retries: int = 3     # bounded in-process delivery attempts before deferring to restart
    # Which side(s) of each card to voice: "back" (default — today's behavior, Back only) or "both"
    # (Front question + Back answer). The default keeps output byte-identical.
    voice_sides: str = "back"


def _as_int(key: str, value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{key} must be an integer, got {value!r}") from None


_VOICE_SIDES_CHOICES = ("back", "both")


def _as_voice_sides(value: str) -> str:
    """Normalize ANKIVOICE_VOICE_SIDES (case-insensitive, trimmed) to a valid choice."""
    normalized = value.strip().lower()
    if normalized not in _VOICE_SIDES_CHOICES:
        raise ConfigError(
            "ANKIVOICE_VOICE_SIDES must be one of "
            f"{_VOICE_SIDES_CHOICES}, got {value!r}"
        )
    return normalized


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build a :class:`Config` from ``env`` (defaults to ``os.environ`` after loading ``.env``)."""
    if env is None:
        # Convenience for operators; env stays authoritative. Safe no-op if python-dotenv/.env absent.
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:  # pragma: no cover - dotenv is a declared dep, but never fail config on it
            pass
        env = os.environ

    missing = [k for k in _REQUIRED if not env.get(k)]
    if missing:
        raise ConfigError("Missing required environment configuration: " + ", ".join(missing))

    model_dir_val = env.get("ANKIVOICE_MODEL_DIR")
    return Config(
        bot_token=env["ANKIVOICE_BOT_TOKEN"],
        archive_chat_id=_as_int("ANKIVOICE_ARCHIVE_CHAT_ID", env["ANKIVOICE_ARCHIVE_CHAT_ID"]),
        default_voice=env.get("ANKIVOICE_DEFAULT_VOICE", "af_heart"),
        lang_code=env.get("ANKIVOICE_LANG_CODE", "a"),
        max_cards=_as_int("ANKIVOICE_MAX_CARDS", env.get("ANKIVOICE_MAX_CARDS", "200")),
        max_file_bytes=_as_int(
            "ANKIVOICE_MAX_FILE_BYTES", env.get("ANKIVOICE_MAX_FILE_BYTES", "2000000")
        ),
        work_dir=Path(env.get("ANKIVOICE_WORK_DIR", "./work")),
        db_path=Path(env.get("ANKIVOICE_DB_PATH", "./data/ankivoice.db")),
        model_dir=Path(model_dir_val) if model_dir_val else None,
        sample_rate=_SAMPLE_RATE,
        mp3_quality=env.get("ANKIVOICE_MP3_QUALITY", "4"),
        job_history=_as_int("ANKIVOICE_JOB_HISTORY", env.get("ANKIVOICE_JOB_HISTORY", "500")),
        ffmpeg_timeout=_as_int("ANKIVOICE_FFMPEG_TIMEOUT", env.get("ANKIVOICE_FFMPEG_TIMEOUT", "120")),
        delivery_retries=_as_int(
            "ANKIVOICE_DELIVERY_RETRIES", env.get("ANKIVOICE_DELIVERY_RETRIES", "3")
        ),
        voice_sides=_as_voice_sides(env.get("ANKIVOICE_VOICE_SIDES", "back")),
    )
