"""Shared helpers/fixtures for the deploy-tooling tests (feature 003).

These tests exercise the install/uninstall scripts and the systemd unit. They MUST stay out of the
fast offline default-suite hot path: every test that needs an external capability (docker,
systemd-analyze, uv, the network, or a bash that can run the scripts) self-skips when that
capability is absent, so a plain ``uv run pytest`` is green on any host (Constitution VII — the
default suite is fast and fully offline).
"""

from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
UNINSTALL_SH = REPO_ROOT / "uninstall.sh"
UNIT_TEMPLATE = REPO_ROOT / "deploy" / "ankivoice.service"


# --- capability probes (used by skipif guards) -------------------------------------------------

def have_bash() -> bool:
    return shutil.which("bash") is not None


def have_uv() -> bool:
    return shutil.which("uv") is not None


def have_systemd_analyze() -> bool:
    return shutil.which("systemd-analyze") is not None


def have_docker() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(
            ["docker", "info"], capture_output=True, timeout=20
        ).returncode == 0
    except Exception:
        return False


def network_available(host: str = "github.com", port: int = 443, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# --- pytest skip markers (module-level convenience) --------------------------------------------

requires_bash = pytest.mark.skipif(not have_bash(), reason="bash not available")
requires_docker = pytest.mark.skipif(not have_docker(), reason="docker not available/usable")
requires_systemd_analyze = pytest.mark.skipif(
    not have_systemd_analyze(), reason="systemd-analyze not available (non-Linux host)"
)
requires_uv = pytest.mark.skipif(not have_uv(), reason="uv not available")


# --- fixtures ----------------------------------------------------------------------------------

@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture()
def stub_bin(tmp_path: Path):
    """Return a factory that creates an executable stub on a temp dir and yields that dir.

    Used to put no-op fakes for apt-get/systemctl/useradd/curl/uv/journalctl on PATH so install.sh
    can be driven without mutating the host. Each stub logs its argv to ``<dir>/<name>.calls``.
    """
    bindir = tmp_path / "stubbin"
    bindir.mkdir()

    def make(name: str, body: str = "", exit_code: int = 0) -> Path:
        p = bindir / name
        log = bindir / f"{name}.calls"
        script = (
            "#!/usr/bin/env bash\n"
            f'echo "$@" >> "{log}"\n'
            f"{body}\n"
            f"exit {exit_code}\n"
        )
        p.write_text(script)
        p.chmod(0o755)
        return p

    make._dir = bindir  # type: ignore[attr-defined]
    return make


def render_unit(user: str, install_dir: str, hf_home: str | None = None) -> str:
    """Render the unit template the same way install.sh does (placeholder substitution)."""
    if hf_home is None:
        hf_home = f"{install_dir}/models"
    text = UNIT_TEMPLATE.read_text()
    return (
        text.replace("{{USER}}", user)
        .replace("{{INSTALL_DIR}}", install_dir)
        .replace("{{HF_HOME}}", hf_home)
    )
