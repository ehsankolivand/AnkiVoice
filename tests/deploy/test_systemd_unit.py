"""FR-008 / FR-011 / FR-017: the systemd unit template renders correctly and is valid.

Asserts the contract directives (research D5 / contracts/deploy-interface.md) and, where
`systemd-analyze` exists (Linux), that the rendered unit passes `systemd-analyze verify`.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from .conftest import UNIT_TEMPLATE, render_unit, requires_systemd_analyze

USER = "ankivoice"
INSTALL_DIR = "/opt/ankivoice"


def _unit() -> str:
    assert UNIT_TEMPLATE.exists(), f"unit template missing: {UNIT_TEMPLATE}"
    return render_unit(USER, INSTALL_DIR)


def test_template_has_placeholders():
    raw = UNIT_TEMPLATE.read_text()
    for ph in ("{{USER}}", "{{INSTALL_DIR}}", "{{HF_HOME}}"):
        assert ph in raw, f"template must use the {ph} placeholder"


def test_required_directives_present():
    u = _unit()
    required = [
        "Type=simple",
        f"User={USER}",
        f"Group={USER}",
        f"WorkingDirectory={INSTALL_DIR}",
        f"EnvironmentFile={INSTALL_DIR}/.env",
        f"Environment=HF_HOME={INSTALL_DIR}/models",
        f"ExecStart={INSTALL_DIR}/.venv/bin/python -m ankivoice",
        "Restart=on-failure",
        "RestartSec=5",
        "TimeoutStopSec=300",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "PrivateTmp=true",
        f"ReadWritePaths={INSTALL_DIR}",
        "WantedBy=multi-user.target",
    ]
    for directive in required:
        assert directive in u, f"missing required unit directive: {directive!r}"


def test_no_inbound_networking_directives():
    """FR-017: a long-polling bot needs no inbound socket/TLS — the unit declares none."""
    u = _unit()
    for forbidden in ("ListenStream", "ListenDatagram", "Sockets=", "Accept="):
        assert forbidden not in u, f"unit should not declare inbound networking: {forbidden!r}"


def test_no_unresolved_placeholders_after_render():
    u = _unit()
    assert "{{" not in u and "}}" not in u, "rendered unit still has unresolved placeholders"


@requires_systemd_analyze
def test_systemd_analyze_verify_clean():
    """On Linux: the rendered unit passes `systemd-analyze verify` (warnings about a missing
    ExecStart binary/user are expected pre-install; we assert no hard syntax errors)."""
    with tempfile.TemporaryDirectory() as td:
        unit = Path(td) / "ankivoice.service"
        unit.write_text(_unit())
        res = subprocess.run(
            ["systemd-analyze", "verify", str(unit)],
            capture_output=True, text=True, timeout=60,
        )
        # verify exits non-zero only on real syntax/parse errors; path/user existence are warnings.
        bad = [
            ln for ln in res.stderr.splitlines()
            if ("Unknown" in ln and "section" in ln) or "Failed to parse" in ln or "Invalid" in ln
        ]
        assert not bad, f"systemd-analyze flagged syntax problems:\n{res.stderr}"
