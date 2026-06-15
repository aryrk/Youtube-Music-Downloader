"""
POST /api/resolve — resolve a YouTube Music URL to track metadata.
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from core.resolver import resolve_url, detect_url_type
from schemas.models import ResolvedContent

router = APIRouter()

TEMP_DIR = Path("/app/temp_cookies")
TEMP_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/resolve", response_model=ResolvedContent)
async def resolve(
    url: str = Form(...),
    cookies_file: UploadFile | None = File(None),
):
    """Resolve a YouTube Music URL and return track metadata."""
    if not url.strip():
        raise HTTPException(status_code=400, detail="URL is required")

    cookies_path: str | None = None
    cookies_session: str | None = None

    if cookies_file and cookies_file.filename:
        session_id = str(uuid.uuid4())
        dest = TEMP_DIR / f"{session_id}.txt"
        with open(dest, "wb") as f:
            shutil.copyfileobj(cookies_file.file, f)
        cookies_path = str(dest)
        cookies_session = session_id

    try:
        content = await resolve_url(url.strip(), cookies_path)
        content.cookies_session = cookies_session
        return content
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/resolve/detect")
async def detect_type(url: str):
    """Quick client-side URL type detection."""
    return {"type": detect_url_type(url)}
