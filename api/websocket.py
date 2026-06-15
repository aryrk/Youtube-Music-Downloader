"""
WebSocket endpoint for real-time job progress updates.
"""
from __future__ import annotations

import logging

from fastapi import WebSocket, WebSocketDisconnect

from core import job_queue

logger = logging.getLogger(__name__)


async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    job_queue.register_ws(ws)
    logger.info("WebSocket client connected")

    try:
        # Send all current jobs immediately on connect
        jobs = job_queue.get_all_jobs()
        await ws.send_json(
            {
                "event": "initial",
                "jobs": [j.to_response().model_dump() for j in jobs],
            }
        )

        # Keep connection alive — client sends periodic pings
        while True:
            try:
                data = await ws.receive_text()
                if data == "ping":
                    await ws.send_text("pong")
            except WebSocketDisconnect:
                break
            except Exception:
                break

    finally:
        job_queue.unregister_ws(ws)
        logger.info("WebSocket client disconnected")
