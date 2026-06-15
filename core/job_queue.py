"""
In-memory job queue with WebSocket broadcast support.
A single asyncio worker task processes jobs sequentially.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import WebSocket
from schemas.models import JobResponse

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Job state
# ---------------------------------------------------------------------------

class Job:
    def __init__(
        self,
        job_id: str,
        video_id: str,
        title: str = "",
        artist: str = "",
        thumbnail: str = "",
        fmt: str = "mp3",
        download_opts: dict | None = None,
    ) -> None:
        self.id = job_id
        self.video_id = video_id
        self.title = title
        self.artist = artist
        self.thumbnail = thumbnail
        self.format = fmt
        self.status = "queued"
        self.progress: float = 0.0
        self.speed: str | None = None
        self.eta: str | None = None
        self.error: str | None = None
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.completed_at: str | None = None
        self.output_path: str | None = None
        self.download_opts: dict = download_opts or {}
        self._cancel_flag = False

    def cancel(self) -> None:
        self._cancel_flag = True
        if self.status in ("queued",):
            self.status = "cancelled"

    def to_response(self) -> JobResponse:
        return JobResponse(
            id=self.id,
            video_id=self.video_id,
            title=self.title,
            artist=self.artist,
            thumbnail=self.thumbnail,
            format=self.format,
            status=self.status,
            progress=self.progress,
            speed=self.speed,
            eta=self.eta,
            error=self.error,
            created_at=self.created_at,
            completed_at=self.completed_at,
            output_path=self.output_path,
        )


# ---------------------------------------------------------------------------
# Queue state (module-level singletons)
# ---------------------------------------------------------------------------

_jobs: dict[str, Job] = {}
_queue: deque[str] = deque()
_lock = asyncio.Lock()
_ws_clients: set[WebSocket] = set()
_worker_task: asyncio.Task | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def enqueue(job: Job) -> None:
    """Add a job to the queue and persist it to DB."""
    async with _lock:
        _jobs[job.id] = job
        _queue.append(job.id)

    # Persist to DB
    try:
        from db.connection import get_db
        async with get_db() as db:
            await db.execute(
                """INSERT OR REPLACE INTO jobs
                   (id, video_id, title, artist, thumbnail, format, status,
                    progress, created_at, download_opts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job.id, job.video_id, job.title, job.artist, job.thumbnail,
                    job.format, job.status, job.progress, job.created_at,
                    json.dumps(job.download_opts),
                ),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to persist job %s: %s", job.id, e)

    await broadcast(job, event="job_added")
    _ensure_worker_running()


async def cancel_job(job_id: str) -> bool:
    """Cancel a job by ID. Returns True if found."""
    job = _jobs.get(job_id)
    if not job:
        return False
    job.cancel()
    await broadcast(job, event="job_update")
    return True


async def remove_job(job_id: str) -> bool:
    """Remove a job from memory (does not cancel active download)."""
    async with _lock:
        if job_id in _jobs:
            del _jobs[job_id]
        try:
            _queue.remove(job_id)
        except ValueError:
            pass
    try:
        from db.connection import get_db
        async with get_db() as db:
            await db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            await db.commit()
    except Exception:
        pass
    return True


async def retry_job(job_id: str) -> Job | None:
    """Clone a failed/cancelled job and re-enqueue it."""
    original = _jobs.get(job_id)
    if not original or original.status not in ("failed", "cancelled"):
        return None
    import uuid
    new_job = Job(
        job_id=str(uuid.uuid4()),
        video_id=original.video_id,
        title=original.title,
        artist=original.artist,
        thumbnail=original.thumbnail,
        fmt=original.format,
        download_opts=original.download_opts,
    )
    await enqueue(new_job)
    return new_job


async def broadcast(job: Job, event: str = "job_update") -> None:
    """Push job state to all connected WebSocket clients."""
    if not _ws_clients:
        return
    payload = json.dumps({"event": event, "job": job.to_response().model_dump()})
    dead: set[WebSocket] = set()
    for ws in list(_ws_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _ws_clients.difference_update(dead)


def get_all_jobs() -> list[Job]:
    """Return all in-memory jobs sorted by created_at descending."""
    return sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def register_ws(ws: WebSocket) -> None:
    _ws_clients.add(ws)


def unregister_ws(ws: WebSocket) -> None:
    _ws_clients.discard(ws)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def _ensure_worker_running() -> None:
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.ensure_future(_run_worker())


async def _run_worker() -> None:
    """
    Single async worker loop — processes jobs from the queue one at a time.
    Catches per-job exceptions without crashing the worker.
    """
    from core.downloader import download_job  # local import to avoid circular

    logger.info("Download worker started")
    while True:
        async with _lock:
            if not _queue:
                break
            job_id = _queue.popleft()

        job = _jobs.get(job_id)
        if job is None or job.status == "cancelled":
            continue

        try:
            await download_job(job)
        except asyncio.CancelledError:
            job.status = "cancelled"
            await broadcast(job)
        except Exception as exc:
            logger.exception("Job %s failed: %s", job.id, exc)
            job.status = "failed"
            job.error = str(exc)
            job.completed_at = datetime.now(timezone.utc).isoformat()
            await broadcast(job)
            await persist_job(job)

    logger.info("Download worker idle")


async def persist_job(job: Job) -> None:
    try:
        from db.connection import get_db
        async with get_db() as db:
            await db.execute(
                """UPDATE jobs SET status=?, progress=?, speed=?, eta=?,
                   error=?, completed_at=?, output_path=? WHERE id=?""",
                (
                    job.status, job.progress, job.speed, job.eta,
                    job.error, job.completed_at, job.output_path, job.id,
                ),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to persist job %s: %s", job.id, e)


async def restore_jobs_from_db() -> None:
    """Load jobs from DB on startup (for history display)."""
    try:
        from db.connection import get_db
        async with get_db() as db:
            async with db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT 200"
            ) as cursor:
                rows = await cursor.fetchall()
        for row in rows:
            d = dict(row)
            job = Job(
                job_id=d["id"],
                video_id=d["video_id"],
                title=d.get("title") or "",
                artist=d.get("artist") or "",
                thumbnail=d.get("thumbnail") or "",
                fmt=d.get("format") or "mp3",
                download_opts=json.loads(d.get("download_opts") or "{}"),
            )
            job.status = d.get("status") or "queued"
            job.progress = d.get("progress") or 0.0
            job.error = d.get("error")
            job.created_at = d.get("created_at") or job.created_at
            job.completed_at = d.get("completed_at")
            job.output_path = d.get("output_path")
            _jobs[job.id] = job
    except Exception as e:
        logger.warning("Failed to restore jobs from DB: %s", e)
