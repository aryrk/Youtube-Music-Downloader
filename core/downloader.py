"""
Download executor — builds yt-dlp options and runs the download for a single Job.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import yt_dlp

from core.converter import FFMPEG_ARGS
from core.job_queue import Job, broadcast, persist_job
from core import metadata as meta_module

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.getenv("DOWNLOAD_DIR", "/app/downloads")
POT_URL = os.getenv("POT_SERVER_URL", "http://pot-provider:4416")


# ---------------------------------------------------------------------------
# Progress hook factory
# ---------------------------------------------------------------------------

def make_progress_hook(job: Job):
    def hook(d: dict) -> None:
        if job._cancel_flag:
            raise yt_dlp.utils.DownloadError("Cancelled by user")

        if d["status"] == "downloading":
            downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
            job.progress = min((downloaded / total) * 100, 99.9)
            job.speed = (d.get("_speed_str") or "").strip() or None
            job.eta = (d.get("_eta_str") or "").strip() or None
            job.status = "downloading"
        elif d["status"] == "finished":
            job.status = "converting"
            job.progress = 100.0
            job.speed = None
            job.eta = None

        # Schedule broadcast without blocking the yt-dlp thread
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(
                asyncio.ensure_future, broadcast(job)
            )
        except Exception:
            pass

    return hook


# ---------------------------------------------------------------------------
# Main download function
# ---------------------------------------------------------------------------

async def download_job(job: Job) -> None:
    """Download and post-process a single job using yt-dlp."""
    opts = job.download_opts
    target_format: str = opts.get("format", "mp3")
    quality_mode: str = opts.get("quality_mode", "quality")
    folder_mode: str = opts.get("folder_mode", "by_album")
    cookies_path: str | None = opts.get("cookies_path")

    # Determine if premium cookies exist
    has_premium = bool(cookies_path and Path(cookies_path).exists())

    # Format selection
    # Premium (itag 141 = 256kbps AAC) vs free (itag 140 = 128kbps AAC)
    if has_premium:
        fmt_sel = "bestaudio[format_id=141]/bestaudio[ext=m4a]/bestaudio/best"
    else:
        # itag 140 is freely available via android client without JS challenge
        fmt_sel = "bestaudio[format_id=140]/bestaudio[ext=m4a]/bestaudio/best"

    # Output template
    if folder_mode == "by_album":
        outtmpl = (
            f"{OUTPUT_DIR}"
            "/%(album_artist|%(uploader|%(channel)s)s)s"
            "/%(album|%(playlist_title|Singles)s)s"
            "/%(track_number|00)02d %(title)s.%(ext)s"
        )
    else:
        outtmpl = (
            f"{OUTPUT_DIR}"
            "/%(album_artist|%(uploader|%(channel)s)s)s"
            " - %(track_number|00)02d %(title)s.%(ext)s"
        )

    # Postprocessors
    postprocessors: list[dict] = []

    if target_format != "m4a":
        ffmpeg_args = FFMPEG_ARGS.get(quality_mode, {}).get(target_format, [])
        postprocessors.append(
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": target_format,
                "preferredquality": "0",
            }
        )

    postprocessors.extend(
        [
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "EmbedThumbnail"},
        ]
    )

    # Postprocessor args for quality control
    pp_args: dict = {}
    if target_format != "m4a":
        ffmpeg_args = FFMPEG_ARGS.get(quality_mode, {}).get(target_format, [])
        if ffmpeg_args:
            pp_args["FFmpegExtractAudio"] = ffmpeg_args

    # Thumbnail crop square
    pp_args["thumbnails"] = ["-vf", "crop='min(iw,ih)':'min(iw,ih)'"]

    # extractor_args: use android/mweb for free (no JS challenge), web_music+POT for premium
    if has_premium:
        extractor_args = {
            "youtube": {"player_client": ["web_music"]},
            "youtubepot-bgutilhttp": {"base_url": [POT_URL]},
        }
    else:
        # android and mweb clients return signed URLs that don't need JS decryption
        extractor_args = {
            "youtube": {"player_client": ["android", "mweb"]},
        }

    ydl_opts: dict = {
        "format": fmt_sel,
        "outtmpl": outtmpl,
        "quiet": True,
        "ignoreerrors": False,
        "writethumbnail": True,
        "progress_hooks": [make_progress_hook(job)],
        "postprocessors": postprocessors,
        "postprocessor_args": pp_args,
        "extractor_args": extractor_args,
    }

    if cookies_path and Path(cookies_path).exists():
        ydl_opts["cookiefile"] = cookies_path

    # Build URL
    video_url = f"https://music.youtube.com/watch?v={job.video_id}"

    job.status = "downloading"
    job.progress = 0.0
    await broadcast(job)

    loop = asyncio.get_event_loop()
    output_file: list[str] = []

    def _run_ydl() -> None:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            if info:
                # Try to determine the output file path
                try:
                    fname = ydl.prepare_filename(info)
                    # After postprocessing the extension changes
                    p = Path(fname)
                    guessed = p.with_suffix(f".{target_format}")
                    if guessed.exists():
                        output_file.append(str(guessed))
                    elif p.exists():
                        output_file.append(str(p))
                except Exception:
                    pass

    try:
        await loop.run_in_executor(None, _run_ydl)
    except Exception as exc:
        if job._cancel_flag:
            job.status = "cancelled"
        else:
            job.status = "failed"
            job.error = str(exc)
        job.completed_at = datetime.now(timezone.utc).isoformat()
        await broadcast(job)
        await persist_job(job)
        return

    # Post-processing: fix MP3 cover art if needed
    if target_format == "mp3" and output_file:
        mp3_path = Path(output_file[0])
        # Look for the source m4a (yt-dlp keeps it temporarily)
        m4a_candidate = mp3_path.with_suffix(".m4a")
        if m4a_candidate.exists():
            meta_module.fix_mp3_cover(m4a_candidate, mp3_path)

    # Update job to done
    job.status = "done"
    job.progress = 100.0
    job.speed = None
    job.eta = None
    job.completed_at = datetime.now(timezone.utc).isoformat()
    if output_file:
        job.output_path = output_file[0]

    await broadcast(job)
    await persist_job(job)

    # Schedule cookie cleanup (30 min TTL)
    if cookies_path:
        asyncio.ensure_future(_delete_after_delay(cookies_path, 1800))


async def _delete_after_delay(path: str, delay: float) -> None:
    await asyncio.sleep(delay)
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass
