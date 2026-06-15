"""
FFmpeg argument presets for each format and quality mode.
Used to populate yt-dlp's postprocessor_args.
"""

FFMPEG_ARGS: dict[str, dict[str, list[str]]] = {
    "quality": {
        "mp3":  ["-c:a", "libmp3lame", "-q:a", "0"],
        "opus": ["-c:a", "libopus", "-b:a", "256k", "-vbr", "on"],
        "ogg":  ["-c:a", "libvorbis", "-q:a", "10"],
        "flac": ["-c:a", "flac", "-compression_level", "0"],
        "wav":  ["-c:a", "pcm_s16le"],
        "m4a":  [],  # native — no re-encode
    },
    "size": {
        "mp3":  ["-c:a", "libmp3lame", "-b:a", "192k"],
        "opus": ["-c:a", "libopus", "-b:a", "128k", "-vbr", "on"],
        "ogg":  ["-c:a", "libvorbis", "-q:a", "5"],
        "flac": ["-c:a", "flac", "-compression_level", "8"],
        "wav":  ["-c:a", "pcm_s16le"],
        "m4a":  [],  # native — no re-encode
    },
}

SUPPORTED_FORMATS = list(FFMPEG_ARGS["quality"].keys())
