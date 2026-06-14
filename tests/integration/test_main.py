"""T028 — entrypoint wiring (offline: run_polling + synthesizer mocked)."""

import ankivoice.__main__ as entry


def test_main_wires_config_store_synth_app_and_starts_polling(monkeypatch, tmp_path):
    monkeypatch.setenv("ANKIVOICE_BOT_TOKEN", "123456:ABC")
    monkeypatch.setenv("ANKIVOICE_ARCHIVE_CHAT_ID", "999")
    monkeypatch.setenv("ANKIVOICE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("ANKIVOICE_DB_PATH", str(tmp_path / "data" / "jobs.sqlite"))
    monkeypatch.setenv("ANKIVOICE_ALLOW_DOWNLOADS", "1")  # don't set process-wide offline env in tests

    ran: dict[str, bool] = {}
    # do not construct/load the real model; do not touch the network
    monkeypatch.setattr(entry, "KokoroSynthesizer", lambda **kwargs: object())
    monkeypatch.setattr(
        "telegram.ext.Application.run_polling",
        lambda self, *a, **k: ran.setdefault("ok", True),
    )

    entry.main()

    assert ran.get("ok") is True
    assert (tmp_path / "data" / "jobs.sqlite").exists()  # durable store opened
