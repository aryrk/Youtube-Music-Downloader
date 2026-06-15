"""
Download management routes:
  POST   /api/download         — enqueue download job(s)
  GET    /api/jobs             — list all jobs
  DELETE /api/jobs/{id}        — cancel/remove a job
  POST   /api/jobs/{id}/retry  — retry a failed/cancelled job
"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from core import job_queue
from core.job_queue import Job
from schemas.models import DownloadRequest, JobResponse

router = APIRouter()

TEMP_DIR = Path("/app/temp_cookies")


def _cookies_path(session_id: str | None) -> str | None:
    if not session_id:
        return None
    p = TEMP_DIR / f"{session_id}.txt"
    return str(p) if p.exists() else None


@router.post("/download")
async def start_download(req: DownloadRequest):
    """Enqueue one or more download jobs."""
    if not req.video_ids:
        raise HTTPException(status_code=400, detail="No video IDs provided")

    cookies_path = _cookies_path(req.cookies_session)

    job_ids: list[str] = []
    for i, video_id in enumerate(req.video_ids):
        fmt = req.format_overrides.get(video_id, req.format)
        job_id = str(uuid.uuid4())

        download_opts = {
            "format": fmt,
            "quality_mode": req.quality_mode,
            "folder_mode": req.folder_mode,
            "cookies_path": cookies_path,
            "album": (req.albums[i] if i < len(req.albums) else ""),
            "album_artist": (req.album_artists[i] if i < len(req.album_artists) else ""),
            "track_number": (req.track_numbers[i] if i < len(req.track_numbers) else 0),
            "track_total": (req.track_totals[i] if i < len(req.track_totals) else 0),
            "year": (req.years[i] if i < len(req.years) else ""),
        }

        job = Job(
            job_id=job_id,
            video_id=video_id,
            title=(req.titles[i] if i < len(req.titles) else video_id),
            artist=(req.artists[i] if i < len(req.artists) else ""),
            thumbnail=(req.thumbnails[i] if i < len(req.thumbnails) else ""),
            fmt=fmt,
            download_opts=download_opts,
        )

        await job_queue.enqueue(job)
        job_ids.append(job_id)

    return {"job_ids": job_ids}


@router.get("/jobs", response_model=list[JobResponse])
async def list_jobs():
    """List all jobs (sorted by created_at descending)."""
    return [j.to_response() for j in job_queue.get_all_jobs()]


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    job = job_queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_response()


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    """Cancel and remove a job."""
    await job_queue.cancel_job(job_id)
    await job_queue.remove_job(job_id)
    return {"status": "removed"}


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: str):
    """Retry a failed or cancelled job."""
    new_job = await job_queue.retry_job(job_id)
    if not new_job:
        raise HTTPException(
            status_code=400,
            detail="Job not found or not in a retryable state",
        )
    return {"job_id": new_job.id}
