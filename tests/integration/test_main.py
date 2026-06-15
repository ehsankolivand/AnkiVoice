"""T028/T016 — entrypoint wiring (offline: run_polling + synthesizer mocked; preflight runs first)."""

import ankivoice.__main__ as entry


def test_main_runs_preflight_before_polling_and_reuses_synth(monkeypatch, tmp_path):
    monkeypatch.setenv("ANKIVOICE_BOT_TOKEN", "123456:ABC")
    monkeypatch.setenv("ANKIVOICE_ARCHIVE_CHAT_ID", "999")
    monkeypatch.setenv("ANKIVOICE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("ANKIVOICE_DB_PATH", str(tmp_path / "data" / "jobs.sqlite"))
    monkeypatch.setenv("ANKIVOICE_ALLOW_DOWNLOADS", "1")  # don't set process-wide offline env in tests

    events: list = []
    sentinel = object()
    # do not construct/load the real model; do not touch the network
    monkeypatch.setattr(entry, "KokoroSynthesizer", lambda **kwargs: sentinel)
    # the startup guard must run BEFORE polling and receive the SAME synthesizer (prewarm reuse)
    monkeypatch.setattr(entry, "check_runtime", lambda cfg, synth: events.append(("preflight", synth)))
    monkeypatch.setattr(
        "telegram.ext.Application.run_polling",
        lambda self, *a, **k: events.append(("poll", None)),
    )

    entry.main()

    assert [e[0] for e in events] == ["preflight", "poll"]  # guard before polling
    assert events[0][1] is sentinel  # same synthesizer prewarmed by the guard
    assert (tmp_path / "data" / "jobs.sqlite").exists()  # durable store opened
