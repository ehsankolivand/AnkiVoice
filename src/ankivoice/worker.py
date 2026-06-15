"""The single speech worker (load-bearing). FR-017,019,021,024,028.

One coroutine drives jobs FCFS. The CPU-bound conversion runs in a thread (``asyncio.to_thread``) and
is awaited before the next job is claimed, so exactly ONE synthesis runs at a time (FR-017). Delivery
is dispatched as a SEPARATE task so it overlaps the next job's synthesis (FR-019). On startup the
worker resumes interrupted work and cleans any already-delivered-but-uncleaned jobs (FR-021).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .cleanup import remove_job_dir
from .config import Config
from .delivery import Sender, deliver
from .errors import ValidationError
from .models import Job, JobState
from .packaging import output_name
from .pipeline import build_package
from .speech import Synthesizer
from .store import JobStore

logger = logging.getLogger("ankivoice.worker")

_GENERIC_FAILURE = (
    "Sorry — something went wrong while processing your deck. Please try again later."
)
# Bound in-flight delivery tasks so a delivery stall can't pile up retained job dirs / memory (P1).
# 2 allows one delivery to overlap the next job's synthesis (FR-019) while staying bounded.
MAX_PENDING_DELIVERIES = 2


class Worker:
    def __init__(
        self,
        *,
        store: JobStore,
        synthesizer: Synthesizer,
        sender: Sender,
        config: Config,
        poll_interval: float = 0.25,
    ) -> None:
        self.store = store
        self.synthesizer = synthesizer
        self.sender = sender
        self.config = config
        self.poll_interval = poll_interval
        self._delivery_tasks: set[asyncio.Task] = set()

    async def resume(self) -> None:
        """Make a restart safe: bound the job table; requeue rebuildable jobs; clean
        delivered-but-uncleaned jobs; and abandon uploads that were interrupted before their input was
        saved (unblocks the user)."""
        pruned = self.store.prune_terminal_jobs(keep=self.config.job_history)
        if pruned:
            logger.info("resume: pruned %d old terminal job row(s)", pruned)
        requeued = self.store.requeue_in_progress()
        if requeued:
            logger.info("resume: requeued %d interrupted job(s)", requeued)
        for job in self.store.list_in_state(JobState.DELIVERED):
            self._safe_cleanup(job.id)
            self.store.set_state(job.id, JobState.CLEANED)
        for job in self.store.list_abandoned_uploads():
            self._safe_cleanup(job.id)
            self.store.set_state(job.id, JobState.FAILED, error_reason="upload_interrupted")

    async def run(self, stop: asyncio.Event) -> None:
        await self.resume()
        try:
            while not stop.is_set():
                # backpressure: don't claim the next job while too many deliveries are in flight (P1)
                while len(self._delivery_tasks) >= MAX_PENDING_DELIVERIES and not stop.is_set():
                    await asyncio.wait(set(self._delivery_tasks), return_when=asyncio.FIRST_COMPLETED)
                if stop.is_set():
                    break
                job = self.store.claim_next()
                if job is None:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
                    except asyncio.TimeoutError:
                        pass
                    continue
                try:
                    await self._process(job)
                except Exception:  # never let one job kill the worker (FR-028, SC-007)
                    logger.exception("unexpected error processing job %s; continuing", job.id)
        finally:
            # let in-flight deliveries finish on shutdown
            if self._delivery_tasks:
                await asyncio.gather(*list(self._delivery_tasks), return_exceptions=True)

    async def _process(self, job: Job) -> None:
        job_dir = Path(self.config.work_dir) / f"job_{job.id}"
        try:
            deck_bytes = Path(job.input_path).read_bytes()
            deck_name = output_name(job.original_filename)
            # CPU-bound; offloaded to a thread and awaited → exactly one synthesis at a time (FR-017).
            apkg_path = await asyncio.to_thread(
                build_package,
                deck_bytes,
                self.synthesizer,
                job_dir=job_dir,
                max_cards=self.config.max_cards,
                deck_name=deck_name,
                mp3_quality=self.config.mp3_quality,
            )
        except ValidationError as exc:
            await self._fail(job, exc.user_message, reason=exc.code)
            return
        except Exception as exc:  # defensive: never let one job kill the worker
            logger.exception("processing failed for job %s", job.id)
            await self._fail(job, _GENERIC_FAILURE, reason=f"error: {exc}")
            return

        # Move OUT of SYNTHESIZING synchronously the instant the build returns, so the worker can claim
        # the next job without ever having two jobs in SYNTHESIZING (the invariant PACKAGING used to
        # serve). deliver() also sets UPLOADING; this synchronous set closes the window before the
        # delivery task is scheduled. (Cycle 002: PACKAGING removed.)
        self.store.set_state(job.id, JobState.UPLOADING)
        # dispatch delivery separately so it overlaps the NEXT job's synthesis (FR-019)
        task = asyncio.create_task(self._deliver(job, apkg_path))
        self._delivery_tasks.add(task)
        task.add_done_callback(self._delivery_tasks.discard)

    async def _deliver(self, job: Job, apkg_path: Path) -> None:
        try:
            await deliver(
                job,
                apkg_path,
                sender=self.sender,
                store=self.store,
                archive_chat_id=self.config.archive_chat_id,
                work_root=self.config.work_dir,
            )
        except Exception:
            # Retain the package and leave the job non-terminal so a restart resumes it (FR-026).
            logger.exception("delivery failed for job %s; retained for resume", job.id)

    async def _fail(self, job: Job, user_message: str, *, reason: str) -> None:
        self.store.set_state(job.id, JobState.FAILED, error_reason=reason)
        try:
            await self.sender.send_message(job.chat_id, user_message)
        except Exception:
            logger.exception("could not notify user about failed job %s", job.id)
        self._safe_cleanup(job.id)  # terminal failure → scoped cleanup (FR-024)

    def _safe_cleanup(self, job_id: int) -> None:
        try:
            remove_job_dir(Path(self.config.work_dir) / f"job_{job_id}", work_root=self.config.work_dir)
        except ValueError:
            logger.error("refused to clean an out-of-scope path for job %s", job_id)
        except Exception:  # e.g. OSError from rmtree — must never kill the worker (FR-028)
            logger.exception("cleanup failed for job %s; continuing", job_id)
