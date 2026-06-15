"""Full end-to-end deploy proof in a throwaway Debian+systemd container (FR-001/006/008/009/010/012/013).

Heavy and opt-in: marked ``container`` (deselected by the default suite) and skipped when docker is
unavailable. Delegates the orchestration to ``run_container_e2e.sh`` (clearer for docker exec) and
asserts it exits 0 — which means every in-script assertion passed (service active/enabled + preflight
green + idempotent .env + refusals + clean --purge). See quickstart.md §D–G.
"""

from __future__ import annotations

import subprocess

import pytest

from .conftest import REPO_ROOT, requires_docker

HARNESS = REPO_ROOT / "tests" / "deploy" / "run_container_e2e.sh"


@pytest.mark.container
@requires_docker
def test_install_uninstall_end_to_end():
    assert HARNESS.exists(), "e2e harness missing"
    proc = subprocess.run(
        ["bash", str(HARNESS)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=2400,  # up to 40 min (model download)
    )
    # Surface the harness transcript on failure for debugging.
    print(proc.stdout[-6000:])
    print(proc.stderr[-3000:])
    assert proc.returncode == 0, "container e2e failed — see captured PASS/FAIL transcript above"
