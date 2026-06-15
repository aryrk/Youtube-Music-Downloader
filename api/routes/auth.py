"""
Playwright-based Google authentication routes.
Provides an in-browser login flow via noVNC.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

from schemas.models import AuthStartResponse, AuthStatusResponse

logger = logging.getLogger(__name__)
router = APIRouter()

TEMP_DIR = Path("/app/temp_cookies")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# In-memory auth sessions: session_id → {browser, context, status, display, vnc_proc}
_auth_sessions: dict[str, dict] = {}

VNC_BASE_PORT = 5900
NOVNC_BASE_PORT = 6080


def _next_display() -> int:
    """Find an available X display number."""
    used = {s.get("display", 99) for s in _auth_sessions.values()}
    for i in range(1, 20):
        if i not in used:
            return i
    return 1


@router.post("/auth/start", response_model=AuthStartResponse)
async def auth_start():
    """Launch a Playwright Chromium browser and expose it via noVNC."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Playwright not installed. Rebuild Docker image with Playwright support.",
        )

    session_id = str(uuid.uuid4())
    display_num = _next_display()
    display = f":{display_num}"
    vnc_port = VNC_BASE_PORT + display_num
    novnc_port = NOVNC_BASE_PORT + display_num

    # Start Xvfb
    xvfb_proc = await asyncio.create_subprocess_exec(
        "Xvfb", display, "-screen", "0", "1280x800x24",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.sleep(1)

    # Start x11vnc
    vnc_proc = await asyncio.create_subprocess_exec(
        "x11vnc", "-display", display, "-nopw", "-listen", "localhost",
        "-xkb", "-forever", "-port", str(vnc_port),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.sleep(0.5)

    # Start websockify (noVNC proxy)
    novnc_proc = await asyncio.create_subprocess_exec(
        "websockify", "--web", "/app/novnc", str(novnc_port),
        f"localhost:{vnc_port}",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.sleep(0.5)

    # Launch Playwright with the virtual display
    env = {**os.environ, "DISPLAY": display}
    playwright_obj = await async_playwright().start()
    browser = await playwright_obj.chromium.launch(
        headless=False,
        env=env,
        args=["--no-sandbox", "--disable-gpu"],
    )
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://accounts.google.com/signin")

    _auth_sessions[session_id] = {
        "playwright": playwright_obj,
        "browser": browser,
        "context": context,
        "status": "pending",
        "display": display_num,
        "xvfb_proc": xvfb_proc,
        "vnc_proc": vnc_proc,
        "novnc_proc": novnc_proc,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    vnc_url = f"/novnc/vnc.html?host={{}}&port={novnc_port}&autoconnect=true"
    return AuthStartResponse(session_id=session_id, vnc_url=f"/novnc/?port={novnc_port}")


@router.get("/auth/status/{session_id}", response_model=AuthStatusResponse)
async def auth_status(session_id: str):
    """Check whether the user has completed Google login."""
    session = _auth_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session["status"] == "authenticated":
        return AuthStatusResponse(status="authenticated", session=session_id)

    # Check for SAPISID cookie
    try:
        context = session["context"]
        cookies = await context.cookies(["https://music.youtube.com"])
        has_auth = any(c["name"] == "SAPISID" for c in cookies)
        if has_auth:
            session["status"] = "authenticated"
            return AuthStatusResponse(status="authenticated", session=session_id)
    except Exception as e:
        logger.warning("Auth status check failed: %s", e)

    return AuthStatusResponse(status="pending")


@router.post("/auth/complete/{session_id}")
async def auth_complete(session_id: str):
    """Export cookies from the Playwright session and save to file."""
    session = _auth_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        context = session["context"]
        cookies = await context.cookies()

        # Write Netscape cookie file format
        cookie_path = TEMP_DIR / f"{session_id}.txt"
        with open(cookie_path, "w") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for c in cookies:
                domain = c.get("domain", "")
                flag = "TRUE" if domain.startswith(".") else "FALSE"
                secure = "TRUE" if c.get("secure") else "FALSE"
                expires = int(c.get("expires", 0)) if c.get("expires", 0) > 0 else 0
                name = c.get("name", "")
                value = c.get("value", "")
                path = c.get("path", "/")
                f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")

        session["status"] = "authenticated"
        session["cookie_path"] = str(cookie_path)
        return {"status": "ok", "session_id": session_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/auth/session/{session_id}")
async def auth_revoke(session_id: str):
    """Close the browser session and delete cookies."""
    session = _auth_sessions.pop(session_id, None)
    if not session:
        return {"status": "not_found"}

    try:
        await session["browser"].close()
        await session["playwright"].stop()
    except Exception:
        pass

    for proc_key in ("novnc_proc", "vnc_proc", "xvfb_proc"):
        proc = session.get(proc_key)
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass

    cookie_path = session.get("cookie_path")
    if cookie_path:
        Path(cookie_path).unlink(missing_ok=True)

    return {"status": "revoked"}
