"""
GET /api/preview/{video_id} — stream audio for in-browser preview.
Forwards Range headers so seeking works in <audio> elements.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import httpx
import yt_dlp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter()

POT_URL = os.getenv("POT_SERVER_URL", "http://pot-provider:4416")

# In-memory cache: video_id → (url, expires_at)
_stream_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 600  # 10 minutes


def _cookies_path(session_id: str | None) -> str | None:
    if not session_id:
        return None
    p = Path("/app/temp_cookies") / f"{session_id}.txt"
    return str(p) if p.exists() else None


async def _get_stream_url(video_id: str, cookies_path: str | None) -> str:
    """Extract the direct audio stream URL via yt-dlp."""
    now = time.time()
    cached = _stream_cache.get(video_id)
    if cached and cached[1] > now:
        return cached[0]

    url = f"https://music.youtube.com/watch?v={video_id}"

    opts: dict = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {
            "youtube": {"player_client": ["web_music"]},
            "youtubepot-bgutilhttp": {"base_url": [POT_URL]},
        },
    }
    if cookies_path:
        opts["cookiefile"] = cookies_path

    loop = asyncio.get_event_loop()

    def _extract() -> str:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
            # Try formats list first, then top-level url
            formats = info.get("formats") or []
            for fmt in reversed(formats):
                if fmt.get("url") and fmt.get("acodec") != "none":
                    return fmt["url"]
            return info.get("url", "")

    stream_url = await loop.run_in_executor(None, _extract)
    if not stream_url:
        raise RuntimeError(f"Could not extract stream URL for {video_id}")

    _stream_cache[video_id] = (stream_url, now + _CACHE_TTL)
    return stream_url


@router.get("/preview/{video_id}")
async def stream_preview(
    video_id: str,
    request: Request,
    cookies_session: str | None = None,
):
    """Stream audio for browser preview, supporting Range requests."""
    try:
        cookies_path = _cookies_path(cookies_session)
        stream_url = await _get_stream_url(video_id, cookies_path)
    except Exception as exc:
        logger.warning("Preview failed for %s: %s", video_id, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    headers: dict[str, str] = {}
    range_header = request.headers.get("Range")
    if range_header:
        headers["Range"] = range_header

    async def _stream():
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            async with client.stream("GET", stream_url, headers=headers) as resp:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    yield chunk

    # Make a HEAD-like request to get content type and length
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            head = await client.head(stream_url, headers=headers)
        content_type = head.headers.get("content-type", "audio/mp4")
        content_length = head.headers.get("content-length")
        content_range = head.headers.get("content-range")
        status_code = 206 if range_header else 200
    except Exception:
        content_type = "audio/mp4"
        content_length = None
        content_range = None
        status_code = 200

    response_headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": content_type,
    }
    if content_length:
        response_headers["Content-Length"] = content_length
    if content_range:
        response_headers["Content-Range"] = content_range

    return StreamingResponse(
        _stream(),
        status_code=status_code,
        headers=response_headers,
        media_type=content_type,
    )
