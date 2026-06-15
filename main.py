"""
YTM Downloader — FastAPI application entrypoint.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routes import resolve, download, preview, library, auth
from api.websocket import websocket_endpoint
from core import job_queue
from db.connection import init_db, close_db
from db.migrations import run_migrations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("Starting YTM Downloader backend...")

    # Initialize DB
    await init_db()
    from db.connection import get_db
    async with get_db() as db:
        await run_migrations(db)

    # Restore jobs from DB
    await job_queue.restore_jobs_from_db()

    logger.info("Backend ready.")
    yield

    logger.info("Shutting down...")
    await close_db()


app = FastAPI(
    title="YTM Downloader",
    description="YouTube Music downloader with React frontend",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# API routes — must be registered BEFORE static files mount
# ---------------------------------------------------------------------------

app.include_router(resolve.router, prefix="/api", tags=["resolve"])
app.include_router(download.router, prefix="/api", tags=["download"])
app.include_router(preview.router, prefix="/api", tags=["preview"])
app.include_router(library.router, prefix="/api", tags=["library"])
app.include_router(auth.router, prefix="/api", tags=["auth"])


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    import httpx
    import os
    pot_url = os.getenv("POT_SERVER_URL", "http://pot-provider:4416")
    pot_status = "unknown"
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{pot_url}/")
            pot_status = "ok" if resp.status_code < 500 else "error"
    except Exception:
        pot_status = "error"

    return JSONResponse({"status": "ok", "pot_provider": pot_status})


# WebSocket
@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await websocket_endpoint(ws)


# ---------------------------------------------------------------------------
# Static files — MUST come last so API routes take priority
# ---------------------------------------------------------------------------

import os
from pathlib import Path

novnc_dir = Path("novnc")
if novnc_dir.exists():
    app.mount("/novnc", StaticFiles(directory="novnc", html=True), name="novnc")

static_dir = Path("static")
if static_dir.exists():
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
else:
    @app.get("/")
    async def root():
        return JSONResponse(
            {"message": "Frontend not built. Run docker compose build."},
            status_code=503,
        )
