"""Scoped deletion (load-bearing guarantee — Constitution Principle V).

The single chokepoint for removing job files. It refuses to delete anything that is not strictly
inside the configured work root (after resolving symlinks and ``..``), so cleanup can NEVER touch a
path outside a job's own working directory (FR-024, FR-025).
"""

from __future__ import annotations

import shutil
from pathlib import Path


def remove_job_dir(job_dir: Path | str, *, work_root: Path | str) -> None:
    """Recursively remove ``job_dir`` iff it is strictly inside ``work_root``; else raise ValueError.

    Idempotent: a no-op if the directory is already gone.
    """
    work_root_resolved = Path(work_root).resolve()
    target = Path(job_dir).resolve()

    if target == work_root_resolved or work_root_resolved not in target.parents:
        raise ValueError(
            f"refusing to delete {target!r}: it is not strictly inside the work root "
            f"{work_root_resolved!r}"
        )

    if target.exists() or target.is_symlink():
        shutil.rmtree(target)
