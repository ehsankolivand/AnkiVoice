"""Delivery + cleanup orchestration (load-bearing). FR-022,023,026,027,029.

Sends the package to the operator archive FIRST, then to the user, then a friendly ready message, and
only after BOTH uploads succeed removes the job's working dir (scoped). On any upload failure the
package is retained (not deleted) so a restart can resume it. Runs as a separate task from synthesis
so delivery overlaps the next job's synthesis (FR-019).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from .cleanup import remove_job_dir
from .models import Job, JobState
from .store import JobStore

logger = logging.getLogger("ankivoice.delivery")

READY_MESSAGE = (
    "✅ Your audio deck is ready! Import it into Anki — when you reveal each answer the audio plays "
    "automatically, and there's a replay button to hear it again."
)


class Sender(Protocol):
    """Outbound interface implemented by the Telegram layer; faked in tests."""

    async def send_document(
        self, chat_id: int, path: Path, *, filename: str, caption: str | None = None
    ) -> None: ...

    async def send_message(self, chat_id: int, text: str) -> None: ...


async def deliver(
    job: Job,
    apkg_path: Path,
    *,
    sender: Sender,
    store: JobStore,
    archive_chat_id: int,
    work_root: Path,
) -> None:
    """Deliver ``apkg_path`` for ``job``: archive → user → ready message → scoped cleanup."""
    apkg_path = Path(apkg_path)
    filename = apkg_path.name

    store.set_state(job.id, JobState.UPLOADING)
    # 1) operator archive backup FIRST (FR-022). If this raises, the package is retained (FR-026).
    await sender.send_document(
        archive_chat_id, apkg_path, filename=filename, caption=f"AnkiVoice backup: {filename}"
    )
    # 2) the requesting user.
    await sender.send_document(job.chat_id, apkg_path, filename=filename)
    # Both copies are out — mark DELIVERED immediately so a crash here does not re-deliver (FR-023).
    store.set_state(job.id, JobState.DELIVERED)
    # 3) friendly confirmation — BEST-EFFORT: a failure here must not prevent cleanup (FR-024).
    try:
        await sender.send_message(job.chat_id, READY_MESSAGE)
    except Exception:
        logger.warning("ready-message send failed for job %s; proceeding to cleanup", job.id)
    # 4) only now remove the job's working dir, scoped (FR-023, FR-025, P5).
    remove_job_dir(Path(work_root) / f"job_{job.id}", work_root=work_root)
    store.set_state(job.id, JobState.CLEANED)
