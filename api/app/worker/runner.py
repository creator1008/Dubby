"""Job queue worker.

Polls the ``jobs`` table, claims queued jobs atomically, and dispatches them
to registered handlers. Designed for a single small Lightsail instance:

- ``WORKER_CONCURRENCY`` bounds simultaneous jobs (default 1 — Demucs and
  ffmpeg are memory/CPU heavy).
- Heartbeats are written via job progress updates; a reaper marks stale
  ``running`` jobs as failed so crashed workers cannot wedge a project.
- SIGTERM/SIGINT drain gracefully: in-flight jobs finish, no new claims.

Run:
    python -m app.worker.runner
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import tempfile
from pathlib import Path
from uuid import UUID

from ..config import Settings, get_settings
from ..db import create_repository
from ..db.base import Repository, Row
from ..storage import R2Storage
from .errors import JobCancelled
from .pipeline import PIPELINE_HANDLERS, JobContext

logger = logging.getLogger("dubby.worker")

_STALE_REAP_INTERVAL_SECONDS = 60.0

# Touched every poll iteration; the container healthcheck asserts freshness.
HEARTBEAT_FILE = Path(
    os.environ.get(
        "WORKER_HEARTBEAT_FILE",
        str(Path(tempfile.gettempdir()) / "dubby-worker-heartbeat"),
    )
)


def _touch_heartbeat() -> None:
    with contextlib.suppress(OSError):
        HEARTBEAT_FILE.touch()


class Worker:
    def __init__(self, settings: Settings, repo: Repository, storage: R2Storage) -> None:
        self._settings = settings
        self._repo = repo
        self._storage = storage
        self._semaphore = asyncio.Semaphore(settings.worker_concurrency)
        self._stopping = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()

    def request_stop(self) -> None:
        logger.info("shutdown requested; draining in-flight jobs")
        self._stopping.set()

    async def run(self) -> None:
        reaper = asyncio.create_task(self._reap_stale_jobs_loop())
        heartbeat = asyncio.create_task(self._heartbeat_loop())
        try:
            while not self._stopping.is_set():
                await self._semaphore.acquire()
                if self._stopping.is_set():
                    self._semaphore.release()
                    break
                job = await self._claim_safe()
                if job is None:
                    self._semaphore.release()
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(
                            self._stopping.wait(),
                            timeout=self._settings.worker_poll_interval_seconds,
                        )
                    continue
                task = asyncio.create_task(self._run_job(job))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        finally:
            for task in (reaper, heartbeat):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if self._tasks:
                logger.info("waiting for %d in-flight job(s)", len(self._tasks))
                await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _claim_safe(self) -> Row | None:
        try:
            return await self._repo.claim_next_job()
        except Exception:
            logger.exception("failed to claim job; backing off")
            await asyncio.sleep(self._settings.worker_poll_interval_seconds)
            return None

    async def _run_job(self, job: Row) -> None:
        job_id = UUID(str(job["id"]))
        kind = str(job["kind"])
        try:
            handler = PIPELINE_HANDLERS.get(kind)
            if handler is None:
                raise RuntimeError(f"no handler registered for job kind {kind!r}")
            ctx = JobContext(
                job_id=job_id,
                project_id=UUID(str(job["project_id"])),
                repo=self._repo,
                storage=self._storage,
                settings=self._settings,
            )
            logger.info("job %s (%s) started", job_id, kind)
            await handler(ctx)
            await self._repo.finish_job(job_id, status="completed", progress=1.0)
            logger.info("job %s completed", job_id)
        except JobCancelled:
            # Someone else moved the job out of `running` (user cancellation
            # or the stale reaper); its status is already final — leave it.
            logger.info("job %s cancelled mid-run; work abandoned", job_id)
        except Exception as exc:
            logger.exception("job %s failed", job_id)
            with contextlib.suppress(Exception):
                await self._repo.finish_job(
                    job_id, status="failed", error=str(exc)[:500]
                )
        finally:
            self._semaphore.release()

    async def _heartbeat_loop(self) -> None:
        while True:
            _touch_heartbeat()
            await asyncio.sleep(15)

    async def _reap_stale_jobs_loop(self) -> None:
        while True:
            try:
                reaped = await self._repo.fail_stale_jobs(
                    self._settings.worker_job_timeout_seconds
                )
                if reaped:
                    logger.warning("reaped %d stale running job(s)", reaped)
            except Exception:
                logger.exception("stale-job reaper iteration failed")
            await asyncio.sleep(_STALE_REAP_INTERVAL_SECONDS)


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    repo = create_repository(settings)
    await repo.startup()
    worker = Worker(settings, repo, R2Storage(settings))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.request_stop)
        except NotImplementedError:  # Windows / restricted environments
            signal.signal(sig, lambda *_: worker.request_stop())

    logger.info(
        "worker started (backend=%s concurrency=%d)",
        settings.db_backend,
        settings.worker_concurrency,
    )
    try:
        await worker.run()
    finally:
        await repo.shutdown()
        logger.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
