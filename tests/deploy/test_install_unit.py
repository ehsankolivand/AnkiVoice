"""Pure-shell unit tests for install.sh helpers (no docker, no root, no host mutation).

install.sh is written to be source-friendly: it defines functions and only runs ``main`` when
executed directly, so these tests can ``source`` it and call individual helpers in a sandbox.
Covers: distro guard, non-root refusal, .env render (0600 + keys), .env no-clobber, secret never
printed, and arg/env precedence. (FR-007, FR-012, FR-015; SC-002, SC-007.)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from .conftest import INSTALL_SH, REPO_ROOT, requires_bash

pytestmark = requires_bash


def _src(snippet: str, env: dict | None = None, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    """Source install.sh and run a snippet (functions only — main does not run when sourced)."""
    full_env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
    if env:
        full_env.update(env)
    return subprocess.run(
        ["bash", "-c", f"source '{INSTALL_SH}'; {snippet}"],
        cwd=cwd, env=full_env, capture_output=True, text=True, timeout=30,
    )


def _write_osrelease(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "os-release"
    p.write_text(content)
    return p


def test_install_sh_exists_and_is_executable():
    assert INSTALL_SH.exists(), "install.sh missing"
    assert os.access(INSTALL_SH, os.X_OK), "install.sh must be executable"


# --- distro guard (FR-012, SC-007) -------------------------------------------------------------

@pytest.mark.parametrize("osrel", [
    'ID=debian\nVERSION_ID="12"\n',
    'ID=ubuntu\nVERSION_ID="22.04"\n',
    'ID=linuxmint\nID_LIKE="ubuntu debian"\n',   # derivative via ID_LIKE
    'ID=pop\nID_LIKE=debian\n',
])
def test_supported_distros_accepted(tmp_path, osrel):
    f = _write_osrelease(tmp_path, osrel)
    res = _src("is_supported_distro", env={"OS_RELEASE_FILE": str(f)})
    assert res.returncode == 0, f"should accept:\n{osrel}\nstderr={res.stderr}"


@pytest.mark.parametrize("osrel", [
    'ID=fedora\nID_LIKE="rhel centos"\n',
    'ID=arch\n',
    'ID=alpine\n',
])
def test_unsupported_distros_refused(tmp_path, osrel):
    f = _write_osrelease(tmp_path, osrel)
    res = _src("is_supported_distro", env={"OS_RELEASE_FILE": str(f)})
    assert res.returncode != 0, f"should refuse:\n{osrel}"
    assert "debian" in res.stderr.lower() or "ubuntu" in res.stderr.lower()


def test_missing_osrelease_refused(tmp_path):
    res = _src("is_supported_distro", env={"OS_RELEASE_FILE": str(tmp_path / "nope")})
    assert res.returncode != 0


# --- non-root refusal makes no changes (FR-012, SC-007) ----------------------------------------

@pytest.mark.skipif(os.geteuid() == 0, reason="this refusal test must run as non-root")
def test_full_run_refuses_non_root_without_mutating(tmp_path):
    prefix = tmp_path / "opt" / "ankivoice"
    res = subprocess.run(
        ["bash", str(INSTALL_SH), "--token", "123:ABC", "--archive-id", "-100",
         "--prefix", str(prefix), "--non-interactive"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
    )
    assert res.returncode != 0, "must refuse when not root"
    assert "root" in (res.stderr + res.stdout).lower()
    assert not prefix.exists(), "must not create the install dir before the root guard"


# --- .env render: 0600 + the two keys + model dir (FR-007) -------------------------------------

def test_write_env_file_perms_and_contents(tmp_path):
    envf = tmp_path / ".env"
    res = _src(
        f"write_env_file '{envf}' 'TOKEN_SECRET_123' '-1009876' '{tmp_path}/models'"
    )
    assert res.returncode == 0, res.stderr
    assert envf.exists()
    assert (envf.stat().st_mode & 0o777) == 0o600, "the .env must be created mode 0600"
    text = envf.read_text()
    assert "ANKIVOICE_BOT_TOKEN=TOKEN_SECRET_123" in text
    assert "ANKIVOICE_ARCHIVE_CHAT_ID=-1009876" in text
    assert f"ANKIVOICE_MODEL_DIR={tmp_path}/models" in text


def test_write_env_file_never_prints_secret(tmp_path):
    envf = tmp_path / ".env"
    res = _src(f"write_env_file '{envf}' 'SUPERSECRETTOKEN' '-100' '{tmp_path}/m'")
    combined = res.stdout + res.stderr
    assert "SUPERSECRETTOKEN" not in combined, "the token must never be echoed to stdout/stderr"


# --- .env no-clobber idempotency (FR-010, SC-002) ----------------------------------------------

def test_ensure_env_file_preserves_existing_byte_for_byte(tmp_path):
    envf = tmp_path / ".env"
    original = "ANKIVOICE_BOT_TOKEN=OPERATOR_EDIT\nANKIVOICE_ARCHIVE_CHAT_ID=42\n# hand comment\n"
    envf.write_text(original)
    envf.chmod(0o600)
    res = _src(f"ensure_env_file '{envf}' 'NEW_TOKEN' '999' '{tmp_path}/m'")
    assert res.returncode == 0, res.stderr
    assert envf.read_text() == original, "an existing .env must be preserved byte-for-byte"
    assert "NEW_TOKEN" not in (res.stdout + res.stderr)


def test_ensure_env_file_creates_when_absent(tmp_path):
    envf = tmp_path / ".env"
    res = _src(f"ensure_env_file '{envf}' 'TOK' '7' '{tmp_path}/m'")
    assert res.returncode == 0, res.stderr
    assert envf.exists() and "ANKIVOICE_BOT_TOKEN=TOK" in envf.read_text()


# --- clarification Q2: warm-up cache derivation matches what the service reads (FR-006) ---------

def test_effective_model_dir_fresh_env_uses_models_subdir(tmp_path):
    envf = tmp_path / ".env"
    envf.write_text(f"ANKIVOICE_BOT_TOKEN=x\nANKIVOICE_MODEL_DIR={tmp_path}/models\n")
    res = _src(f"effective_model_dir '{envf}' '{tmp_path}'")
    assert res.stdout.strip() == f"{tmp_path}/models"


def test_effective_model_dir_preserved_env_with_key_honored(tmp_path):
    envf = tmp_path / ".env"
    envf.write_text("ANKIVOICE_MODEL_DIR=/custom/cache\n")
    res = _src(f"effective_model_dir '{envf}' '{tmp_path}'")
    assert res.stdout.strip() == "/custom/cache"


def test_effective_model_dir_preserved_env_strips_quotes(tmp_path):
    """systemd's EnvironmentFile strips matching quotes — the warm-up must resolve the same path."""
    envf = tmp_path / ".env"
    envf.write_text('ANKIVOICE_MODEL_DIR="/quoted/cache"\n')
    res = _src(f"effective_model_dir '{envf}' '{tmp_path}'")
    assert res.stdout.strip() == "/quoted/cache"


def test_effective_model_dir_preserved_env_without_key_falls_back_to_home_cache(tmp_path):
    """Case 3: a preserved .env without the key -> the service-user default HF cache under the dir."""
    envf = tmp_path / ".env"
    envf.write_text("ANKIVOICE_BOT_TOKEN=x\nANKIVOICE_ARCHIVE_CHAT_ID=1\n")
    res = _src(f"effective_model_dir '{envf}' '/opt/ankivoice'")
    assert res.stdout.strip() == "/opt/ankivoice/.cache/huggingface"


# --- value resolution precedence: flag > env (FR-007) ------------------------------------------

def test_resolve_required_prefers_flag_over_env():
    res = _src("resolve_required NAME flagval envval")
    assert res.stdout.strip() == "flagval"


def test_resolve_required_falls_back_to_env():
    res = _src("resolve_required NAME '' envval")
    assert res.stdout.strip() == "envval"


def test_resolve_required_empty_returns_nonzero():
    res = _src("resolve_required NAME '' ''")
    assert res.returncode != 0
