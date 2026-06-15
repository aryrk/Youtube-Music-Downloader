"""
Database migrations — runs at startup to ensure schema is up to date.
"""
import aiosqlite


async def run_migrations(db: aiosqlite.Connection) -> None:
    """Create tables if they don't exist."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id           TEXT PRIMARY KEY,
            video_id     TEXT NOT NULL,
            title        TEXT,
            artist       TEXT,
            thumbnail    TEXT,
            format       TEXT,
            status       TEXT DEFAULT 'queued',
            progress     REAL DEFAULT 0,
            speed        TEXT,
            eta          TEXT,
            error        TEXT,
            created_at   TEXT,
            completed_at TEXT,
            output_path  TEXT,
            download_opts TEXT
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id         TEXT PRIMARY KEY,
            cookies    TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)

    await db.commit()
