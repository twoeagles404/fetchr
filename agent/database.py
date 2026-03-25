"""
Fetchr SQLite Database Layer
Inspired by CyberDropDownloader's history/hash tracking approach.

Tables:
  downloads — persistent queue (survives agent restarts)
  history   — URL-based deduplication (skip already-downloaded URLs)
  hashes    — hash-based deduplication (detect same file at different URLs)
"""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import aiosqlite

DB_PATH = Path(__file__).parent / "fetchr.db"


async def init_db() -> None:
    """Create all tables and indexes if they don't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            -- Persistent download queue ─────────────────────────────────────
            CREATE TABLE IF NOT EXISTS downloads (
                id          TEXT PRIMARY KEY,
                url         TEXT NOT NULL,
                filename    TEXT,
                save_path   TEXT,
                dl_type     TEXT DEFAULT 'direct',
                referer     TEXT DEFAULT '',
                cookies     TEXT DEFAULT '',
                status      TEXT DEFAULT 'queued',
                progress    REAL DEFAULT 0.0,
                speed       REAL DEFAULT 0.0,
                downloaded  INTEGER DEFAULT 0,
                total       INTEGER DEFAULT 0,
                segments    INTEGER DEFAULT 1,
                error       TEXT,
                added_at    TEXT,
                finished_at TEXT
            );

            -- URL-based download history (CDL-style dedup) ──────────────────
            CREATE TABLE IF NOT EXISTS history (
                url           TEXT PRIMARY KEY,
                filename      TEXT,
                save_path     TEXT,
                file_size     INTEGER DEFAULT 0,
                downloaded_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            -- Hash-based dedup: same file, different URL ────────────────────
            CREATE TABLE IF NOT EXISTS hashes (
                hash       TEXT PRIMARY KEY,
                algorithm  TEXT DEFAULT 'sha256',
                filename   TEXT,
                file_path  TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            -- Indexes ────────────────────────────────────────────────────────
            CREATE INDEX IF NOT EXISTS idx_downloads_status   ON downloads(status);
            CREATE INDEX IF NOT EXISTS idx_downloads_added_at ON downloads(added_at);
            CREATE INDEX IF NOT EXISTS idx_history_url        ON history(url);
        """)
        await db.commit()


# ── History ──────────────────────────────────────────────────────────────────

async def check_history(url: str) -> bool:
    """Return True if this URL was already downloaded."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM history WHERE url = ?", (url,)
        ) as cur:
            return await cur.fetchone() is not None


async def add_to_history(
    url: str,
    filename: str,
    save_path: str,
    file_size: int = 0,
) -> None:
    """Record a completed download in history."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO history
               (url, filename, save_path, file_size, downloaded_at)
               VALUES (?, ?, ?, ?, ?)""",
            (url, filename, save_path, file_size,
             datetime.utcnow().isoformat()),
        )
        await db.commit()


async def get_history(limit: int = 500) -> List[Dict]:
    """Return recent download history, newest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM history ORDER BY downloaded_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── Hash deduplication ───────────────────────────────────────────────────────

async def check_hash(file_hash: str) -> Optional[str]:
    """Return filename if this hash already exists in the DB, else None."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT filename FROM hashes WHERE hash = ?", (file_hash,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def add_hash(
    file_hash: str,
    filename: str,
    file_path: str,
    algorithm: str = "sha256",
) -> None:
    """Store a file hash after a successful download."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO hashes
               (hash, algorithm, filename, file_path, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (file_hash, algorithm, filename, file_path,
             datetime.utcnow().isoformat()),
        )
        await db.commit()


def compute_hash(file_path: Path, algorithm: str = "sha256") -> str:
    """
    Compute a file hash reading in 1 MB chunks (CDL-style).
    Keeps memory usage constant regardless of file size.
    """
    h = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Persistent queue ─────────────────────────────────────────────────────────

async def save_download(item: Dict) -> None:
    """Upsert a download item to the persistent queue table."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO downloads
               (id, url, filename, save_path, dl_type, referer, cookies,
                status, progress, speed, downloaded, total, segments,
                error, added_at, finished_at)
               VALUES
               (:id, :url, :filename, :save_path, :dl_type, :referer, :cookies,
                :status, :progress, :speed, :downloaded, :total, :segments,
                :error, :added_at, :finished_at)""",
            {
                "id":          item.get("id"),
                "url":         item.get("url"),
                "filename":    item.get("filename"),
                "save_path":   item.get("save_path"),
                "dl_type":     item.get("dl_type", "direct"),
                "referer":     item.get("referer", ""),
                "cookies":     item.get("cookies", ""),
                "status":      item.get("status", "queued"),
                "progress":    item.get("progress", 0.0),
                "speed":       item.get("speed", 0.0),
                "downloaded":  item.get("downloaded", 0),
                "total":       item.get("total", 0),
                "segments":    item.get("segments", 1),
                "error":       item.get("error"),
                "added_at":    item.get("added_at"),
                "finished_at": item.get("finished_at"),
            },
        )
        await db.commit()


async def load_pending_downloads() -> List[Dict]:
    """
    Load downloads that were queued or active when the agent last stopped.
    Active downloads will be re-queued (they were interrupted mid-flight).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM downloads
               WHERE status IN ('queued', 'active')
               ORDER BY added_at ASC"""
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def delete_download(item_id: str) -> None:
    """Remove a single download from the persistent store."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM downloads WHERE id = ?", (item_id,))
        await db.commit()


async def clear_finished_from_db() -> None:
    """Purge completed / cancelled / errored downloads from persistent store."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM downloads "
            "WHERE status IN ('complete', 'cancelled', 'error')"
        )
        await db.commit()
