"""FR-005 / cycle-002 regression: `uv sync` must keep the spaCy model en_core_web_sm.

A plain `uv sync` is exact and prunes anything not in the lockfile; before this feature the spaCy
English model (a separate GitHub-release wheel, not a PyPI package) was never locked, so it was
dropped. The fix pins it by direct wheel URL (research D3). These tests guard that it cannot recur.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from .conftest import REPO_ROOT, network_available, requires_uv

LOCK = REPO_ROOT / "uv.lock"


def test_lockfile_pins_en_core_web_sm_with_url_and_hash():
    """Always-on (no network/uv needed): the lock entry exists, by URL, with a sha256 hash."""
    text = LOCK.read_text()
    # Locate the [[package]] stanza (not the dependency-list reference) for en-core-web-sm.
    stanzas = text.split("[[package]]")
    pkg = [s for s in stanzas if re.search(r'^\s*name = "en-core-web-sm"', s, re.M)]
    assert pkg, "en-core-web-sm package stanza missing from uv.lock (uv sync would drop the model)"
    block = pkg[0]
    # pinned by the GitHub release wheel URL
    assert "spacy-models/releases/download/en_core_web_sm-3.8.0" in block, "model not pinned by URL"
    # locked with a sha256 hash (reproducible)
    assert 'hash = "sha256:' in block, "model wheel not locked with a sha256 hash"


@pytest.mark.container
@requires_uv
@pytest.mark.skipif(not network_available(), reason="network unavailable for a clean uv sync")
def test_clean_uv_sync_keeps_model_importable():
    """End-to-end: a clean locked sync into a throwaway env keeps en_core_web_sm importable."""
    with tempfile.TemporaryDirectory() as td:
        env_dir = Path(td) / "venv"
        # Build the project's locked env into an isolated venv path (does not touch the repo .venv).
        base_env = {
            "PATH": __import__("os").environ["PATH"],
            "HOME": td,
            "UV_PROJECT_ENVIRONMENT": str(env_dir),
        }
        sync = subprocess.run(
            ["uv", "sync", "--locked", "--no-dev"],
            cwd=REPO_ROOT, env=base_env, capture_output=True, text=True, timeout=900,
        )
        assert sync.returncode == 0, f"uv sync failed:\n{sync.stderr}"
        py = env_dir / ("Scripts" if __import__("os").name == "nt" else "bin") / "python"
        check = subprocess.run(
            [str(py), "-c", "import en_core_web_sm; print(en_core_web_sm.__version__)"],
            capture_output=True, text=True, timeout=120,
        )
        assert check.returncode == 0, f"model not importable after sync:\n{check.stderr}"
        assert check.stdout.strip().startswith("3.8"), check.stdout
