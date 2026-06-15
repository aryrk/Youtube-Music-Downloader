"""
Database connection management.
Single aiosqlite connection opened on startup, shared via module-level variable.
"""
import aiosqlite
from pathlib import Path

DB_PATH = Path("/app/data/ytm.db")

_db: aiosqlite.Connection | None = None


async def init_db() -> None:
    """Open the database connection and create data dir if needed."""
    global _db
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(str(DB_PATH))
    _db.row_factory = aiosqlite.Row


async def close_db() -> None:
    """Close the database connection on shutdown."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None


class _DBContextManager:
    async def __aenter__(self) -> aiosqlite.Connection:
        if _db is None:
            raise RuntimeError("Database not initialized. Call init_db() first.")
        return _db

    async def __aexit__(self, *args) -> None:
        pass  # Connection is long-lived; do not close here.


def get_db() -> "_DBContextManager":
    """Return an async context manager yielding the shared db connection."""
    return _DBContextManager()
