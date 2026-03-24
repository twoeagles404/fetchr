"""
Fetchr Local Agent
FastAPI server + WebSocket broadcaster for the browser extension.

Run with:  python main.py
Or:        uvicorn main:app --host 127.0.0.1 --port 9876 --reload
"""

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Optional, Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from queue_manager import QueueManager, DownloadType, DownloadStatus

# ------------------------------------------------------------------ #
# Config                                                               #
# ------------------------------------------------------------------ #

DEFAULT_SAVE_PATH = str(Path.home() / "Downloads" / "Fetchr")
PORT = int(os.environ.get("FETCHR_PORT", 9876))

# ------------------------------------------------------------------ #
# App setup                                                            #
# ------------------------------------------------------------------ #

app = FastAPI(title="Fetchr Agent", version="1.0.0")

# Allow the browser extension (chrome-extension://*) to reach us
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

queue = QueueManager(max_concurrent=3)
_ws_clients: Set[WebSocket] = set()

# ------------------------------------------------------------------ #
# WebSocket broadcaster                                                #
# ------------------------------------------------------------------ #

async def _broadcast(payload: dict):
    """Send a progress update to all connected WebSocket clients."""
    dead = set()
    msg = json.dumps({"type": "progress", "data": payload})
    for ws in list(_ws_clients):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    _ws_clients -= dead


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    # Send current queue snapshot on connect
    await ws.send_text(json.dumps({"type": "snapshot", "data": queue.list_all()}))
    try:
        while True:
            await ws.receive_text()   # keep-alive / accept pings
    except WebSocketDisconnect:
        _ws_clients.discard(ws)


# ------------------------------------------------------------------ #
# Startup                                                              #
# ------------------------------------------------------------------ #

@app.on_event("startup")
async def startup():
    queue.on_progress(_broadcast)
    queue.start()
    Path(DEFAULT_SAVE_PATH).mkdir(parents=True, exist_ok=True)
    print(f"\n✅  Fetchr agent listening on http://127.0.0.1:{PORT}")
    print(f"   Save folder: {DEFAULT_SAVE_PATH}\n")


# ------------------------------------------------------------------ #
# Settings (in-memory; persisted to settings.json)                    #
# ------------------------------------------------------------------ #

SETTINGS_FILE = Path(__file__).parent / "settings.json"

_settings = {
    "save_path":      DEFAULT_SAVE_PATH,
    "max_concurrent": 3,
    "max_speed_kbps": 0,       # 0 = unlimited
    "intercept_all":  True,
}


def _load_settings():
    global _settings
    if SETTINGS_FILE.exists():
        try:
            _settings.update(json.loads(SETTINGS_FILE.read_text()))
        except Exception:
            pass


def _save_settings():
    SETTINGS_FILE.write_text(json.dumps(_settings, indent=2))


_load_settings()


@app.get("/settings")
async def get_settings():
    return _settings


class SettingsUpdate(BaseModel):
    save_path:      Optional[str]  = None
    max_concurrent: Optional[int]  = None
    max_speed_kbps: Optional[int]  = None
    intercept_all:  Optional[bool] = None


@app.put("/settings")
async def update_settings(body: SettingsUpdate):
    if body.save_path      is not None: _settings["save_path"]      = body.save_path
    if body.max_concurrent is not None:
        _settings["max_concurrent"] = body.max_concurrent
        queue.max_concurrent = body.max_concurrent
    if body.max_speed_kbps is not None: _settings["max_speed_kbps"] = body.max_speed_kbps
    if body.intercept_all  is not None: _settings["intercept_all"]  = body.intercept_all
    _save_settings()
    return _settings


# ------------------------------------------------------------------ #
# Health                                                               #
# ------------------------------------------------------------------ #

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0", "active": _count_active()}


def _count_active() -> int:
    return sum(
        1 for d in queue.list_all()
        if d["status"] in (DownloadStatus.ACTIVE, DownloadStatus.QUEUED)
    )


# ------------------------------------------------------------------ #
# Downloads CRUD                                                        #
# ------------------------------------------------------------------ #

class AddDownloadRequest(BaseModel):
    url:       str
    filename:  Optional[str] = None
    save_path: Optional[str] = None
    dl_type:   Optional[str] = "direct"   # "direct" | "media"
    referer:   Optional[str] = None       # page that triggered the download
    cookies:   Optional[str] = None       # raw Cookie header from the browser


def _guess_filename(url: str) -> str:
    """Extract filename from URL or generate a placeholder."""
    path = url.split("?")[0].rstrip("/")
    name = path.split("/")[-1]
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name or "download"


@app.post("/downloads", status_code=201)
async def add_download(body: AddDownloadRequest):
    filename  = body.filename  or _guess_filename(body.url)
    save_path = body.save_path or _settings["save_path"]
    dl_type   = DownloadType(body.dl_type) if body.dl_type else DownloadType.DIRECT

    item = queue.add(
        url=body.url,
        filename=filename,
        save_path=save_path,
        dl_type=dl_type,
        referer=body.referer or "",
        cookies=body.cookies or "",
    )
    return item.to_dict()


@app.get("/downloads")
async def list_downloads():
    return queue.list_all()


@app.get("/downloads/{item_id}")
async def get_download(item_id: str):
    item = queue.get(item_id)
    if not item:
        raise HTTPException(404, "Download not found")
    return item.to_dict()


@app.post("/downloads/{item_id}/pause")
async def pause_download(item_id: str):
    ok = await queue.pause(item_id)
    if not ok:
        raise HTTPException(400, "Cannot pause this download")
    return {"ok": True}


@app.post("/downloads/{item_id}/resume")
async def resume_download(item_id: str):
    ok = await queue.resume(item_id)
    if not ok:
        raise HTTPException(400, "Cannot resume this download")
    return {"ok": True}


@app.delete("/downloads/{item_id}")
async def cancel_download(item_id: str):
    cancelled = await queue.cancel(item_id)
    removed   = queue.remove(item_id)
    if not removed and not cancelled:
        raise HTTPException(404, "Download not found")
    return {"ok": True}


@app.post("/downloads/clear-finished")
async def clear_finished():
    queue.clear_finished()
    return {"ok": True}


# ------------------------------------------------------------------ #
# Media scanner                                                        #
# ------------------------------------------------------------------ #

class ScanRequest(BaseModel):
    url: str


@app.post("/scan")
async def scan_url(body: ScanRequest):
    from downloader import scan_media
    formats = await scan_media(body.url)
    return {"url": body.url, "formats": formats}


# ------------------------------------------------------------------ #
# Entrypoint                                                           #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=PORT, log_level="info")
