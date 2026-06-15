"""
Metadata fixups via mutagen.
Primarily used to ensure cover art is correctly embedded in MP3 files
after yt-dlp's postprocessor chain.
"""
from __future__ import annotations

from pathlib import Path


def fix_mp3_cover(source_m4a: Path, mp3_path: Path) -> None:
    """
    Copy cover art from the source m4a into the mp3 ID3 tags.
    Non-fatal — audio remains valid if this fails.
    """
    try:
        from mutagen.mp4 import MP4, MP4Cover
        from mutagen.id3 import ID3, APIC

        m4a_tags = MP4(str(source_m4a)).tags or {}
        if "covr" not in m4a_tags:
            return

        cover_atom = m4a_tags["covr"][0]
        mime = (
            "image/png"
            if cover_atom.imageformat == MP4Cover.FORMAT_PNG
            else "image/jpeg"
        )

        id3 = ID3(str(mp3_path))
        id3.delall("APIC")
        id3.add(
            APIC(
                encoding=3,
                mime=mime,
                type=3,
                desc="Cover",
                data=bytes(cover_atom),
            )
        )
        id3.save(v2_version=3)
    except Exception:
        pass  # Non-fatal


def tag_file(file_path: Path, tags: dict) -> None:
    """
    Apply arbitrary tag dict to an audio file using mutagen's auto-detection.
    Supports mp3, m4a, ogg, flac, opus.
    """
    try:
        import mutagen
        audio = mutagen.File(str(file_path), easy=True)
        if audio is None:
            return
        for key, value in tags.items():
            if value:
                try:
                    audio[key] = str(value)
                except Exception:
                    pass
        audio.save()
    except Exception:
        pass
