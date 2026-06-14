"""T005 — config loads from env only (Principle VIII), with documented defaults."""

from pathlib import Path

import pytest

from ankivoice.config import Config, ConfigError, load_config


def base_env(**over: str) -> dict[str, str]:
    env = {
        "ANKIVOICE_BOT_TOKEN": "tok:123",
        "ANKIVOICE_ARCHIVE_CHAT_ID": "-100123",
    }
    env.update(over)
    return env


def test_loads_required_and_defaults():
    cfg = load_config(base_env())
    assert isinstance(cfg, Config)
    assert cfg.bot_token == "tok:123"
    assert cfg.archive_chat_id == -100123
    # documented defaults (research.md)
    assert cfg.default_voice == "af_heart"
    assert cfg.lang_code == "a"
    assert cfg.max_cards == 200
    assert cfg.max_file_bytes == 2_000_000
    assert cfg.sample_rate == 24000
    assert isinstance(cfg.work_dir, Path)
    assert isinstance(cfg.db_path, Path)
    assert cfg.model_dir is None


def test_overrides_from_env():
    cfg = load_config(
        base_env(
            ANKIVOICE_DEFAULT_VOICE="am_michael",
            ANKIVOICE_LANG_CODE="b",
            ANKIVOICE_MAX_CARDS="50",
            ANKIVOICE_MAX_FILE_BYTES="1000",
            ANKIVOICE_WORK_DIR="/tmp/w",
            ANKIVOICE_DB_PATH="/tmp/db.sqlite",
            ANKIVOICE_MODEL_DIR="/tmp/models",
        )
    )
    assert cfg.default_voice == "am_michael"
    assert cfg.lang_code == "b"
    assert cfg.max_cards == 50
    assert cfg.max_file_bytes == 1000
    assert cfg.work_dir == Path("/tmp/w")
    assert cfg.db_path == Path("/tmp/db.sqlite")
    assert cfg.model_dir == Path("/tmp/models")


def test_missing_required_lists_every_missing_key():
    with pytest.raises(ConfigError) as ei:
        load_config({})
    msg = str(ei.value)
    assert "ANKIVOICE_BOT_TOKEN" in msg
    assert "ANKIVOICE_ARCHIVE_CHAT_ID" in msg


def test_invalid_int_values_raise_configerror():
    with pytest.raises(ConfigError):
        load_config(base_env(ANKIVOICE_ARCHIVE_CHAT_ID="not-an-int"))
    with pytest.raises(ConfigError):
        load_config(base_env(ANKIVOICE_MAX_CARDS="abc"))


def test_config_is_frozen():
    cfg = load_config(base_env())
    with pytest.raises(Exception):
        cfg.bot_token = "changed"  # type: ignore[misc]


def test_does_not_read_real_process_env(monkeypatch):
    # load_config must use the passed mapping, not os.environ, when a mapping is given.
    monkeypatch.setenv("ANKIVOICE_BOT_TOKEN", "leak")
    cfg = load_config(base_env(ANKIVOICE_BOT_TOKEN="explicit"))
    assert cfg.bot_token == "explicit"
