"""
GET /api/library — list downloaded files grouped by album directory.
"""
from __future__ import annotations

import os
from pathlib import Path

import mutagen
from fastapi import APIRouter

from schemas.models import LibraryAlbum, LibraryTrack

router = APIRouter()

DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/app/downloads"))
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".ogg", ".flac", ".wav", ".opus"}


def _read_track_meta(file_path: Path) -> dict:
    """Use mutagen to read basic track metadata."""
    title = file_path.stem
    try:
        audio = mutagen.File(str(file_path), easy=True)
        if audio and audio.tags:
            title = str(audio.tags.get("title", [file_path.stem])[0])
    except Exception:
        pass
    return {"title": title}


@router.get("/library", response_model=list[LibraryAlbum])
async def get_library():
    """Walk the downloads directory and return albums grouped by folder."""
    if not DOWNLOAD_DIR.exists():
        return []

    # Group files by parent directory
    album_map: dict[Path, list[Path]] = {}
    for file_path in DOWNLOAD_DIR.rglob("*"):
        if file_path.suffix.lower() in AUDIO_EXTENSIONS:
            parent = file_path.parent
            album_map.setdefault(parent, []).append(file_path)

    albums: list[LibraryAlbum] = []
    for folder, files in sorted(album_map.items()):
        files = sorted(files)
        # Album name = folder name; artist = parent folder name (if nested)
        album_name = folder.name
        try:
            relative = folder.relative_to(DOWNLOAD_DIR)
            parts = relative.parts
            artist_name = parts[0] if len(parts) > 1 else ""
        except ValueError:
            artist_name = ""

        tracks: list[LibraryTrack] = []
        for f in files:
            meta = _read_track_meta(f)
            tracks.append(
                LibraryTrack(
                    filename=f.name,
                    title=meta["title"],
                    size_bytes=f.stat().st_size,
                    format=f.suffix.lstrip("."),
                    path=str(f),
                )
            )

        albums.append(
            LibraryAlbum(
                album=album_name,
                artist=artist_name,
                path=str(folder),
                tracks=tracks,
            )
        )

    return albums
