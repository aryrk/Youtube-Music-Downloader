"""
Playwright-based Google authentication via a persistent virtual display.
Xvfb + x11vnc + websockify start at container launch (not per-session).
The noVNC viewer at port 6080 is exposed in docker-compose.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from schemas.models import AuthStartResponse, AuthStatusResponse

logger = logging.getLogger(__name__)
router = APIRouter()

TEMP_DIR = Path("/app/temp_cookies")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

_sessions: dict[str, dict] = {}

_playwright_obj = None
_browser = None
_browser_lock = asyncio.Lock()


async def _ensure_browser():
    """Lazily start a single Playwright Chromium instance on the virtual display."""
    global _playwright_obj, _browser
    async with _browser_lock:
        if _browser and _browser.is_connected():
            return _browser
        try:
            from playwright.async_api import async_playwright
            if _playwright_obj:
                try:
                    await _playwright_obj.stop()
                except Exception:
                    pass
            _playwright_obj = await async_playwright().start()
            env = {**os.environ, "DISPLAY": ":99"}
            _browser = await _playwright_obj.chromium.launch(
                headless=False,
                env=env,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                    "--window-size=1280,800",
                    "--start-maximized",
                ],
            )
            logger.info("Playwright Chromium launched on DISPLAY=:99")
            return _browser
        except Exception as exc:
            logger.error("Failed to launch Playwright browser: %s", exc)
            raise


@router.post("/auth/start", response_model=AuthStartResponse)
async def auth_start(request: Request):
    """
    Open a Google sign-in page in the shared Chromium instance.
    The user watches and interacts via the noVNC viewer at port 6080.
    """
    session_id = str(uuid.uuid4())
    _sessions[session_id] = {"status": "starting", "context": None, "cookie_path": None}

    try:
        browser = await _ensure_browser()
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            java_script_enabled=True,
            accept_downloads=False,
        )

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = {
                app: { isInstalled: false },
                runtime: {
                    onMessage: { addListener: () => {} },
                    connect: () => {}
                }
            };
        """)
        page = await context.new_page()
        await page.goto("https://accounts.google.com/signin", wait_until="domcontentloaded")
        _sessions[session_id]["context"] = context
        _sessions[session_id]["status"] = "pending"
    except Exception as exc:
        _sessions[session_id]["status"] = "error"
        _sessions[session_id]["error"] = str(exc)
        logger.error("Auth start failed: %s", exc)
        raise HTTPException(status_code=503, detail=f"Could not launch browser: {exc}")

    host = request.headers.get("host", "localhost").split(":")[0]
    vnc_url = f"http://{host}:6080/vnc.html?autoconnect=true&resize=scale&quality=6"
    return AuthStartResponse(session_id=session_id, vnc_url=vnc_url)


@router.get("/auth/status/{session_id}", response_model=AuthStatusResponse)
async def auth_status(session_id: str):
    """Poll to detect when the user has completed sign-in."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session["status"] in ("authenticated", "error"):
        return AuthStatusResponse(
            status=session["status"],
            session=session_id if session["status"] == "authenticated" else None,
        )

    context = session.get("context")
    if not context:
        return AuthStatusResponse(status=session["status"])

    try:
        cookies = await context.cookies(["https://music.youtube.com"])
        if any(c["name"] == "SAPISID" for c in cookies):
            session["status"] = "authenticated"
            return AuthStatusResponse(status="authenticated", session=session_id)
    except Exception as exc:
        logger.warning("Cookie check failed: %s", exc)

    return AuthStatusResponse(status="pending")


@router.post("/auth/complete/{session_id}")
async def auth_complete(session_id: str):
    """Export cookies from the Playwright context and persist to disk."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    context = session.get("context")
    if not context:
        raise HTTPException(status_code=400, detail="No browser context for this session")

    try:
        cookies = await context.cookies()
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

        try:
            await context.close()
        except Exception:
            pass

        return {"status": "ok", "session_id": session_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/auth/session/{session_id}")
async def auth_revoke(session_id: str):
    """Close the browser context and delete stored cookies."""
    session = _sessions.pop(session_id, None)
    if not session:
        return {"status": "not_found"}

    context = session.get("context")
    if context:
        try:
            await context.close()
        except Exception:
            pass

    cookie_path = session.get("cookie_path")
    if cookie_path:
        Path(cookie_path).unlink(missing_ok=True)

    return {"status": "revoked"}


@router.get("/auth/sessions")
async def list_sessions():
    """Return active auth sessions (for the frontend to check connection status)."""
    return [
        {"session_id": sid, "status": s["status"], "has_cookies": bool(s.get("cookie_path"))}
        for sid, s in _sessions.items()
    ]
