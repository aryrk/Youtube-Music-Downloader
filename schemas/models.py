"""
Pydantic models / schemas for the YTM Downloader API.
"""
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

class TrackMeta(BaseModel):
    video_id: str
    title: str
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    track_number: int = 0
    track_total: int = 0
    duration_seconds: int = 0
    year: str = ""
    thumbnail: str = ""
    is_available: bool = True


class ResolvedContent(BaseModel):
    type: str  # "song" | "album" | "playlist" | "artist"
    id: str
    title: str
    artist: str = ""
    year: str = ""
    thumbnail: str = ""
    track_count: int = 0
    tracks: List[TrackMeta] = Field(default_factory=list)
    cookies_session: Optional[str] = None


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

class DownloadRequest(BaseModel):
    video_ids: List[str]
    titles: List[str] = Field(default_factory=list)
    artists: List[str] = Field(default_factory=list)
    thumbnails: List[str] = Field(default_factory=list)
    albums: List[str] = Field(default_factory=list)
    album_artists: List[str] = Field(default_factory=list)
    track_numbers: List[int] = Field(default_factory=list)
    track_totals: List[int] = Field(default_factory=list)
    years: List[str] = Field(default_factory=list)
    format: str = "mp3"
    quality_mode: str = "quality"   # "quality" | "size"
    folder_mode: str = "by_album"   # "by_album" | "flat"
    cookies_session: Optional[str] = None
    format_overrides: dict[str, str] = Field(default_factory=dict)


class JobResponse(BaseModel):
    id: str
    video_id: str
    title: str = ""
    artist: str = ""
    thumbnail: str = ""
    format: str = "mp3"
    status: str = "queued"
    progress: float = 0.0
    speed: Optional[str] = None
    eta: Optional[str] = None
    error: Optional[str] = None
    created_at: str = ""
    completed_at: Optional[str] = None
    output_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------

class LibraryTrack(BaseModel):
    filename: str
    title: str
    size_bytes: int
    format: str
    path: str


class LibraryAlbum(BaseModel):
    album: str
    artist: str
    path: str
    tracks: List[LibraryTrack] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class AuthStartResponse(BaseModel):
    session_id: str
    vnc_url: str


class AuthStatusResponse(BaseModel):
    status: str  # "pending" | "authenticated" | "error"
    session: Optional[str] = None
