"""Pure-shell unit tests for uninstall.sh (no docker/root): the --purge scope guard (FR-013, P5).

uninstall.sh is source-friendly; these source it and exercise ``is_safe_install_dir`` directly.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from .conftest import REPO_ROOT, UNINSTALL_SH, requires_bash

pytestmark = requires_bash


def _safe(dir_arg: str, home: str = "/home/someone") -> int:
    return subprocess.run(
        ["bash", "-c", f"source '{UNINSTALL_SH}'; is_safe_install_dir '{dir_arg}'"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"], "HOME": home},
    ).returncode


def test_uninstall_sh_exists_and_executable():
    assert UNINSTALL_SH.exists() and os.access(UNINSTALL_SH, os.X_OK)


def _clean(dir_arg: str, home: str = "/home/someone") -> str:
    """Return is_safe_install_dir's echoed canonical path (empty string if it refused)."""
    return subprocess.run(
        ["bash", "-c", f"source '{UNINSTALL_SH}'; is_safe_install_dir '{dir_arg}'"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"], "HOME": home},
    ).stdout.strip()


@pytest.mark.parametrize("good", ["/opt/ankivoice", "/srv/ankivoice", "/opt/ankivoice/",
                                  "/home/svc/ankivoice", "/opt//ankivoice"])
def test_scope_guard_accepts_real_install_dirs(good):
    assert _safe(good) == 0, f"{good} should be accepted"


@pytest.mark.parametrize("bad", [
    "/", "/opt", "/usr", "/usr/local/bin", "/etc", "/var", "/root", "/home", "/run",
    "/var/lib", "/lib/x", "relative/path", "",
])
def test_scope_guard_refuses_dangerous_paths(bad):
    assert _safe(bad, home="/home/someone") != 0, f"{bad} should be refused"


def test_scope_guard_refuses_home():
    assert _safe("/home/operator", home="/home/operator") != 0, "$HOME must be refused"


@pytest.mark.parametrize("traversal", [
    "/opt/ankivoice/../../etc",
    "/srv/app/../../etc",
    "/opt/ankivoice/../../../tmp/victim",
    "/opt/./ankivoice/../secret",
    "/opt/ankivoice/..",
])
def test_scope_guard_refuses_path_traversal(traversal):
    """CRITICAL: '..'/'.' components must be refused (no escape outside the footprint)."""
    assert _safe(traversal) != 0, f"traversal path must be refused: {traversal}"


@pytest.mark.parametrize("other_home", ["/home/alice", "/home/bob", "/home/root"])
def test_scope_guard_refuses_other_users_homes(other_home):
    """CRITICAL: a bare /home/<user> (another user's home) must be refused."""
    assert _safe(other_home, home="/home/operator") != 0, f"{other_home} must be refused"


def test_scope_guard_echoes_cleaned_path_for_caller():
    """The validated path the caller deletes is the cleaned form (slashes collapsed)."""
    assert _clean("/opt//ankivoice/") == "/opt/ankivoice"
