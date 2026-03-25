"""
Fetchr Download Queue Manager
SQLite-backed queue with dynamic concurrency, URL dedup, and restart recovery.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Callable, Awaitable

import database as db


class DownloadStatus(str, Enum):
    QUEUED    = "queued"
    ACTIVE    = "active"
    PAUSED    = "paused"
    COMPLETE  = "complete"
    ERROR     = "error"
    CANCELLED = "cancelled"


class DownloadType(str, Enum):
    DIRECT = "direct"
    MEDIA  = "media"


@dataclass
class DownloadItem:
    id:          str
    url:         str
    filename:    str
    save_path:   str
    dl_type:     DownloadType = DownloadType.DIRECT
    referer:     str = ""
    cookies:     str = ""
    status:      DownloadStatus = DownloadStatus.QUEUED
    progress:    float = 0.0
    speed:       float = 0.0
    downloaded:  int = 0
    total:       int = 0
    eta:         int = 0
    segments:    int = 1
    error:       Optional[str] = None
    added_at:    str = field(default_factory=lambda: datetime.utcnow().isoformat())
    finished_at: Optional[str] = None

    # Internal — not persisted
    _task:        Optional[asyncio.Task] = field(default=None, repr=False, compare=False)
    _pause_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)

    def __post_init__(self):
        self._pause_event.set()  # not paused by default

    def to_dict(self) -> dict:
        return {
            "id":          self.id,
            "url":         self.url,
            "filename":    self.filename,
            "save_path":   self.save_path,
            "dl_type":     self.dl_type,
            "status":      self.status,
            "progress":    round(self.progress, 1),
            "speed":       round(self.speed, 0),
            "downloaded":  self.downloaded,
            "total":       self.total,
            "eta":         self.eta,
            "segments":    self.segments,
            "error":       self.error,
            "added_at":    self.added_at,
            "finished_at": self.finished_at,
        }


ProgressCallback = Callable[[dict], Awaitable[None]]


class QueueManager:
    def __init__(self, max_concurrent: int = 3):
        self._max_concurrent = max_concurrent
        self._active_count   = 0
        self._slot_available = asyncio.Event()
        self._slot_available.set()

        self._downloads:          Dict[str, DownloadItem] = {}
        self._queue:              asyncio.Queue = asyncio.Queue()
        self._progress_callbacks: List[ProgressCallback] = []
        self._worker_task:        Optional[asyncio.Task] = None

    # ── Concurrency ──────────────────────────────────────────────────────────

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @max_concurrent.setter
    def max_concurrent(self, value: int):
        self._max_concurrent = value
        self._slot_available.set()   # wake sleeping workers to re-evaluate

    async def _acquire_slot(self):
        """Wait until an active-download slot is free."""
        while True:
            if self._active_count < self._max_concurrent:
                self._active_count += 1
                return
            self._slot_available.clear()
            await self._slot_available.wait()

    def _release_slot(self):
        self._active_count = max(0, self._active_count - 1)
        self._slot_available.set()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self):
        """
        Initialize DB, restore any pending downloads from last session,
        then start the background worker.
        """
        await db.init_db()
        await self._restore_pending()
        self._worker_task = asyncio.create_task(self._worker())

    async def _restore_pending(self):
        """Re-queue downloads that were in-flight when the agent last stopped."""
        pending = await db.load_pending_downloads()
        for row in pending:
            item = DownloadItem(
                id        = row["id"],
                url       = row["url"],
                filename  = row["filename"] or "",
                save_path = row["save_path"] or "",
                dl_type   = DownloadType(row.get("dl_type", "direct")),
                referer   = row.get("referer", ""),
                cookies   = row.get("cookies", ""),
                status    = DownloadStatus.QUEUED,   # reset active → queued
                added_at  = row.get("added_at", datetime.utcnow().isoformat()),
            )
            self._downloads[item.id] = item
            self._queue.put_nowait(item.id)
        if pending:
            print(f"↩  Restored {len(pending)} pending download(s) from last session")

    async def _worker(self):
        while True:
            item_id = await self._queue.get()
            item = self._downloads.get(item_id)
            if not item or item.status != DownloadStatus.QUEUED:
                continue

            await self._acquire_slot()
            item.status = DownloadStatus.ACTIVE
            await self._notify(item)
            await db.save_download(item.to_dict())

            async def run_and_release(it: DownloadItem):
                try:
                    from downloader import run_download
                    await run_download(it, self._notify)
                finally:
                    self._release_slot()
                    # Persist final state
                    await db.save_download(it.to_dict())
                    # On completion, add to history for dedup
                    if it.status == DownloadStatus.COMPLETE:
                        file_path = Path(it.save_path) / it.filename
                        file_size = file_path.stat().st_size if file_path.exists() else 0
                        await db.add_to_history(
                            url       = it.url,
                            filename  = it.filename,
                            save_path = it.save_path,
                            file_size = file_size,
                        )

            item._task = asyncio.create_task(run_and_release(item))

    # ── Public API ────────────────────────────────────────────────────────────

    async def add(
        self,
        url:       str,
        filename:  str,
        save_path: str,
        dl_type:   DownloadType = DownloadType.DIRECT,
        referer:   str = "",
        cookies:   str = "",
    ) -> Optional[DownloadItem]:
        """
        Add a download to the queue.
        Returns None (silently skips) if the URL was already downloaded.
        """
        # URL-based deduplication (CDL-inspired)
        if await db.check_history(url):
            print(f"⏭  Skipping already-downloaded URL: {url}")
            return None

        item = DownloadItem(
            id        = str(uuid.uuid4()),
            url       = url,
            filename  = filename,
            save_path = save_path,
            dl_type   = dl_type,
            referer   = referer,
            cookies   = cookies,
        )
        self._downloads[item.id] = item
        await db.save_download(item.to_dict())
        self._queue.put_nowait(item.id)
        return item

    def get(self, item_id: str) -> Optional[DownloadItem]:
        return self._downloads.get(item_id)

    def list_all(self) -> List[dict]:
        items = sorted(self._downloads.values(), key=lambda x: x.added_at, reverse=True)
        return [i.to_dict() for i in items]

    async def pause(self, item_id: str) -> bool:
        item = self._downloads.get(item_id)
        if not item or item.status != DownloadStatus.ACTIVE:
            return False
        item.status = DownloadStatus.PAUSED
        item._pause_event.clear()
        await self._notify(item)
        await db.save_download(item.to_dict())
        return True

    async def resume(self, item_id: str) -> bool:
        item = self._downloads.get(item_id)
        if not item or item.status != DownloadStatus.PAUSED:
            return False
        item.status = DownloadStatus.ACTIVE
        item._pause_event.set()
        await self._notify(item)
        await db.save_download(item.to_dict())
        return True

    async def cancel(self, item_id: str) -> bool:
        item = self._downloads.get(item_id)
        if not item or item.status in (DownloadStatus.COMPLETE, DownloadStatus.CANCELLED):
            return False
        item.status = DownloadStatus.CANCELLED
        item._pause_event.set()
        if item._task and not item._task.done():
            item._task.cancel()
        await self._notify(item)
        await db.save_download(item.to_dict())
        return True

    def remove(self, item_id: str) -> bool:
        if item_id in self._downloads:
            del self._downloads[item_id]
            asyncio.create_task(db.delete_download(item_id))
            return True
        return False

    def clear_finished(self):
        finished = [
            k for k, v in self._downloads.items()
            if v.status in (
                DownloadStatus.COMPLETE,
                DownloadStatus.CANCELLED,
                DownloadStatus.ERROR,
            )
        ]
        for k in finished:
            del self._downloads[k]
        asyncio.create_task(db.clear_finished_from_db())

    # ── Progress ──────────────────────────────────────────────────────────────

    def on_progress(self, callback: ProgressCallback):
        self._progress_callbacks.append(callback)

    async def _notify(self, item: DownloadItem):
        payload = item.to_dict()
        for cb in self._progress_callbacks:
            try:
                await cb(payload)
            except Exception:
                pass

    async def notify_item(self, item: DownloadItem):
        await self._notify(item)
