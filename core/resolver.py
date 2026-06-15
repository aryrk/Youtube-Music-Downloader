"""
URL resolver — detects content type and extracts track metadata via yt-dlp.
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any
from urllib.parse import urlparse, parse_qs

import yt_dlp

from schemas.models import TrackMeta, ResolvedContent
from core import ytmusic_client

POT_URL = os.getenv("POT_SERVER_URL", "http://pot-provider:4416")


# ---------------------------------------------------------------------------
# URL type detection
# ---------------------------------------------------------------------------

def detect_url_type(url: str) -> str:
    """
    Returns one of: "song", "album", "playlist", "artist"
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    list_val = (qs.get("list") or [""])[0]

    if list_val.startswith("OLAK5uy_"):
        return "album"
    if "/channel/" in parsed.path or "/@" in parsed.path:
        return "artist"
    if list_val and "playlist" in url:
        return "playlist"
    if "/watch" in parsed.path and qs.get("v"):
        return "song"
    # Fallback: if there's a list param at all, treat as playlist
    if list_val:
        return "playlist"
    return "song"


# ---------------------------------------------------------------------------
# Thumbnail helpers
# ---------------------------------------------------------------------------

def _best_thumbnail(thumbnails: list[dict] | None, fallback: str = "") -> str:
    """Pick the highest-resolution thumbnail from a yt-dlp thumbnails list."""
    if not thumbnails:
        return fallback
    # yt-dlp sorts thumbnails ascending by resolution; last = best
    best = thumbnails[-1].get("url", fallback)
    # Make square for YouTube thumbnails
    if best and "ytimg.com" in best:
        best = re.sub(r"=w\d+-h\d+.*$", "", best)
        best += "=w1200-h1200-l90-rj"
    return best


def _entry_thumbnail(entry: dict) -> str:
    """Extract thumbnail from a yt-dlp flat/full entry."""
    thumbs = entry.get("thumbnails") or []
    if thumbs:
        return _best_thumbnail(thumbs)
    return entry.get("thumbnail", "")


# ---------------------------------------------------------------------------
# yt-dlp base options
# ---------------------------------------------------------------------------

def _base_opts(cookies_path: str | None = None) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
    }
    if cookies_path:
        opts["cookiefile"] = cookies_path
        opts["extractor_args"] = {
            "youtube": {"player_client": ["web_music", "tv"]},
            "youtubepot-bgutilhttp": {"base_url": [POT_URL]},
        }
    else:
        opts["extractor_args"] = {
            "youtube": {"player_client": ["android", "ios", "tv", "web"]}
        }
    return opts


# ---------------------------------------------------------------------------
# Entry → TrackMeta mapping
# ---------------------------------------------------------------------------

def _entry_to_track(entry: dict, fallback_album: str = "", fallback_artist: str = "") -> TrackMeta:
    video_id = entry.get("id") or entry.get("url", "").split("v=")[-1]
    title = entry.get("title") or entry.get("track") or "Unknown"
    artist = (
        entry.get("artist")
        or entry.get("uploader")
        or entry.get("channel")
        or fallback_artist
    )
    album = entry.get("album") or fallback_album
    album_artist = entry.get("album_artist") or entry.get("uploader") or artist
    track_number = entry.get("track_number") or entry.get("playlist_index") or 0
    track_total = entry.get("n_entries") or 0
    year = str(entry.get("release_year") or entry.get("upload_date", "")[:4] or "")
    duration = int(entry.get("duration") or 0)
    thumbnail = _entry_thumbnail(entry)
    is_available = entry.get("availability") != "unavailable"

    return TrackMeta(
        video_id=video_id,
        title=title,
        artist=artist,
        album=album,
        album_artist=album_artist,
        track_number=track_number,
        track_total=track_total,
        duration_seconds=duration,
        year=year,
        thumbnail=thumbnail,
        is_available=is_available,
    )


# ---------------------------------------------------------------------------
# Resolve functions
# ---------------------------------------------------------------------------

async def _extract_flat(url: str, cookies_path: str | None) -> dict:
    """Run yt-dlp flat extraction in executor."""
    opts = _base_opts(cookies_path)
    opts["extract_flat"] = True
    loop = asyncio.get_event_loop()

    def _run() -> dict:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False) or {}

    return await loop.run_in_executor(None, _run)


async def _extract_full(url: str, cookies_path: str | None) -> dict:
    """Run yt-dlp full extraction (single video) in executor."""
    opts = _base_opts(cookies_path)
    loop = asyncio.get_event_loop()

    def _run() -> dict:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False) or {}

    return await loop.run_in_executor(None, _run)


async def _resolve_song(url: str, cookies_path: str | None) -> ResolvedContent:
    info = await _extract_full(url, cookies_path)
    track = _entry_to_track(info)
    return ResolvedContent(
        type="song",
        id=track.video_id,
        title=track.title,
        artist=track.artist,
        year=track.year,
        thumbnail=track.thumbnail,
        track_count=1,
        tracks=[track],
    )


async def _resolve_playlist_or_album(
    url: str, content_type: str, cookies_path: str | None
) -> ResolvedContent:
    info = await _extract_flat(url, cookies_path)
    entries = info.get("entries") or []
    title = info.get("title") or info.get("playlist_title") or "Playlist"
    uploader = info.get("uploader") or info.get("channel") or ""
    thumbnail = _best_thumbnail(info.get("thumbnails"), info.get("thumbnail", ""))
    year = str(info.get("release_year") or info.get("upload_date", "")[:4] or "")
    total = len(entries)

    tracks: list[TrackMeta] = []
    for idx, entry in enumerate(entries, 1):
        if not entry:
            continue
        t = _entry_to_track(entry, fallback_album=title, fallback_artist=uploader)
        if t.track_number == 0:
            t = t.model_copy(update={"track_number": idx, "track_total": total})
        elif t.track_total == 0:
            t = t.model_copy(update={"track_total": total})
        tracks.append(t)

    return ResolvedContent(
        type=content_type,
        id=info.get("id") or info.get("playlist_id") or url,
        title=title,
        artist=uploader,
        year=year,
        thumbnail=thumbnail,
        track_count=total,
        tracks=tracks,
    )


async def _resolve_artist(url: str, cookies_path: str | None) -> ResolvedContent:
    """Resolve artist channel via ytmusicapi + yt-dlp per album."""
    # Extract channel ID from URL
    channel_id = await _get_channel_id(url, cookies_path)

    releases = await ytmusic_client.get_artist_releases(channel_id)

    all_tracks: list[TrackMeta] = []
    artist_name = ""
    artist_thumb = ""

    for release in releases:
        try:
            album_content = await _resolve_playlist_or_album(
                release["url"], release["type"], cookies_path
            )
            if not artist_name:
                artist_name = album_content.artist
            if not artist_thumb:
                artist_thumb = album_content.thumbnail
            all_tracks.extend(album_content.tracks)
        except Exception:
            continue

    # If ytmusicapi yielded nothing, fall back to yt-dlp flat on the channel URL
    if not all_tracks:
        return await _resolve_playlist_or_album(url, "artist", cookies_path)

    return ResolvedContent(
        type="artist",
        id=channel_id,
        title=artist_name,
        artist=artist_name,
        year="",
        thumbnail=artist_thumb,
        track_count=len(all_tracks),
        tracks=all_tracks,
    )


async def _get_channel_id(url: str, cookies_path: str | None) -> str:
    """Extract or resolve the bare channel ID from a URL."""
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")

    # Direct /channel/{id}
    if "channel" in parts:
        idx = parts.index("channel")
        if idx + 1 < len(parts):
            return parts[idx + 1]

    # /@handle — resolve via yt-dlp
    info = await _extract_flat(url, cookies_path)
    cid = info.get("channel_id") or info.get("uploader_id") or ""
    if not cid and info.get("entries"):
        first = (info["entries"] or [{}])[0]
        cid = first.get("channel_id") or first.get("uploader_id") or ""
    return cid


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def resolve_url(url: str, cookies_path: str | None = None) -> ResolvedContent:
    """Main entry point — detect URL type and resolve to ResolvedContent."""
    url_type = detect_url_type(url)
    if url_type == "song":
        return await _resolve_song(url, cookies_path)
    elif url_type == "album":
        return await _resolve_playlist_or_album(url, "album", cookies_path)
    elif url_type == "playlist":
        return await _resolve_playlist_or_album(url, "playlist", cookies_path)
    else:  # artist
        return await _resolve_artist(url, cookies_path)
