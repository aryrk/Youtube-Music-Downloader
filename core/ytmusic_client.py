"""
ytmusicapi client wrapper.
Module-level YTMusic instance (no auth needed for public content).
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

from ytmusicapi import YTMusic

_client = YTMusic()

# Simple dict-based cache keyed by channel_id
_artist_cache: dict[str, list[dict]] = {}


@lru_cache(maxsize=128)
def _get_artist_sync(channel_id: str) -> list[dict]:
    """Synchronous ytmusicapi call (runs in executor)."""
    info = _client.get_artist(channel_id)
    releases: list[dict] = []
    for category in ("albums", "singles"):
        section = info.get(category) or {}
        for item in section.get("results", []):
            pid = item.get("playlistId")
            if pid:
                releases.append(
                    {
                        "title": item.get("title", ""),
                        "year": str(item.get("year", "")),
                        "type": category.rstrip("s"),
                        "url": f"https://music.youtube.com/playlist?list={pid}",
                    }
                )
    return releases


async def get_artist_releases(channel_id: str) -> list[dict[str, Any]]:
    """Return all album/single playlist URLs for a channel ID."""
    if channel_id in _artist_cache:
        return _artist_cache[channel_id]
    loop = asyncio.get_event_loop()
    releases = await loop.run_in_executor(None, _get_artist_sync, channel_id)
    _artist_cache[channel_id] = releases
    return releases


async def search_songs(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search YouTube Music for songs."""
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(
        None, lambda: _client.search(query, filter="songs", limit=limit)
    )
    return results or []
