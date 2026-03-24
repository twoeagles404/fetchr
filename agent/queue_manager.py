"""
Fetchr Download Queue Manager
Handles queuing, state tracking, and concurrency limits.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Callable, Awaitable


class DownloadStatus(str, Enum):
    QUEUED    = "queued"
    ACTIVE    = "active"
    PAUSED    = "paused"
    COMPLETE  = "complete"
    ERROR     = "error"
    CANCELLED = "cancelled"


class DownloadType(str, Enum):
    DIRECT = "direct"   # regular file download
    MEDIA  = "media"    # yt-dlp handled


@dataclass
class DownloadItem:
    id: str
    url: str
    filename: str
    save_path: str
    dl_type: DownloadType = DownloadType.DIRECT

    referer: str = ""              # page that initiated the download (for Referer header)
    cookies: str = ""              # raw Cookie header forwarded from the browser

    status: DownloadStatus = DownloadStatus.QUEUED
    progress: float = 0.0          # 0–100
    speed: float = 0.0             # bytes/sec
    downloaded: int = 0            # bytes
    total: int = 0                 # bytes (0 = unknown)
    eta: int = 0                   # seconds
    segments: int = 1              # number of parallel segments (1 = single stream)
    error: Optional[str] = None
    added_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    finished_at: Optional[str] = None

    # internal: asyncio task handle for cancellation
    _task: Optional[asyncio.Task] = field(default=None, repr=False, compare=False)
    _pause_event: asyncio.Event = field(
        default_factory=asyncio.Event, repr=False, compare=False
    )

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


# Callback type: receives the updated DownloadItem dict
ProgressCallback = Callable[[dict], Awaitable[None]]


class QueueManager:
    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max_concurrent
        self._downloads: Dict[str, DownloadItem] = {}
        self._queue: asyncio.Queue = asyncio.Queue()
        self._progress_callbacks: List[ProgressCallback] = []
        self._worker_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def start(self):
        """Start the background worker. Call once on app startup."""
        self._worker_task = asyncio.create_task(self._worker())

    async def _worker(self):
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def bounded_download(item: DownloadItem):
            async with semaphore:
                from downloader import run_download
                await run_download(item, self._notify)

        while True:
            item_id = await self._queue.get()
            item = self._downloads.get(item_id)
            if item and item.status == DownloadStatus.QUEUED:
                item.status = DownloadStatus.ACTIVE
                await self._notify(item)
                item._task = asyncio.create_task(bounded_download(item))

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def add(
        self,
        url: str,
        filename: str,
        save_path: str,
        dl_type: DownloadType = DownloadType.DIRECT,
        referer: str = "",
        cookies: str = "",
    ) -> DownloadItem:
        item = DownloadItem(
            id=str(uuid.uuid4()),
            url=url,
            filename=filename,
            save_path=save_path,
            dl_type=dl_type,
            referer=referer,
            cookies=cookies,
        )
        self._downloads[item.id] = item
        self._queue.put_nowait(item.id)
        return item

    def get(self, item_id: str) -> Optional[DownloadItem]:
        return self._downloads.get(item_id)

    def list_all(self) -> List[dict]:
        # newest first
        items = sorted(
            self._downloads.values(),
            key=lambda x: x.added_at,
            reverse=True,
        )
        return [i.to_dict() for i in items]

    async def pause(self, item_id: str) -> bool:
        item = self._downloads.get(item_id)
        if not item or item.status != DownloadStatus.ACTIVE:
            return False
        item.status = DownloadStatus.PAUSED
        item._pause_event.clear()
        await self._notify(item)
        return True

    async def resume(self, item_id: str) -> bool:
        item = self._downloads.get(item_id)
        if not item or item.status != DownloadStatus.PAUSED:
            return False
        item.status = DownloadStatus.ACTIVE
        item._pause_event.set()
        await self._notify(item)
        return True

    async def cancel(self, item_id: str) -> bool:
        item = self._downloads.get(item_id)
        if not item or item.status in (
            DownloadStatus.COMPLETE,
            DownloadStatus.CANCELLED,
        ):
            return False
        item.status = DownloadStatus.CANCELLED
        item._pause_event.set()   # unblock any paused wait
        if item._task and not item._task.done():
            item._task.cancel()
        await self._notify(item)
        return True

    def remove(self, item_id: str) -> bool:
        if item_id in self._downloads:
            del self._downloads[item_id]
            return True
        return False

    def clear_finished(self):
        finished = [
            k for k, v in self._downloads.items()
            if v.status in (DownloadStatus.COMPLETE, DownloadStatus.CANCELLED, DownloadStatus.ERROR)
        ]
        for k in finished:
            del self._downloads[k]

    # ------------------------------------------------------------------ #
    # Progress broadcast                                                   #
    # ------------------------------------------------------------------ #

    def on_progress(self, callback: ProgressCallback):
        self._progress_callbacks.append(callback)

    async def _notify(self, item: DownloadItem):
        payload = item.to_dict()
        for cb in self._progress_callbacks:
            try:
                await cb(payload)
            except Exception:
                pass

    # convenience for downloader module
    async def notify_item(self, item: DownloadItem):
        await self._notify(item)
