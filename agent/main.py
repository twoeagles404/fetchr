"""
Fetchr Local Agent  v2.0
FastAPI server + WebSocket broadcaster + Web UI

New in v2:
  • Serves a full web UI at http://[host]:9876/
    — accessible from any device on your network
  • POST /scrape   — gallery/batch link grabber (CDL-inspired)
  • GET  /history  — download history
  • DELETE /history/{url} — remove a history entry
  • Async queue.start() with DB init and pending-download recovery

Run with:  python main.py
Or:        uvicorn main:app --host 0.0.0.0 --port 9876
"""

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Set

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.base import BaseHTTPMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from queue_manager import QueueManager, DownloadType, DownloadStatus
import database as db
import notifications
from aria2_rpc import aria2

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_SAVE_PATH = str(Path.home() / "Downloads" / "Fetchr")
PORT = int(os.environ.get("FETCHR_PORT", 9876))
WEB_DIR = Path(__file__).parent / "web"

# ── App ───────────────────────────────────────────────────────────────────────

queue = QueueManager(max_concurrent=3)
_ws_clients: Set[WebSocket] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    await aria2.start_daemon()
    queue.on_progress(_broadcast)
    await queue.start()
    Path(DEFAULT_SAVE_PATH).mkdir(parents=True, exist_ok=True)
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    if _settings.get("notification_url"):
        notifications.configure(_settings["notification_url"])
    print(f"\n✅  Fetchr agent v2.1 listening on http://0.0.0.0:{PORT}")
    print(f"   Web UI:      http://localhost:{PORT}/")
    print(f"   Save folder: {DEFAULT_SAVE_PATH}\n")

    yield  # ← app runs here

    # ── Shutdown ─────────────────────────────────────────────────────────────
    await aria2.stop_daemon()


app = FastAPI(title="Fetchr Agent", version="2.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class _PrivateNetworkMiddleware(BaseHTTPMiddleware):
    """Allow Chrome extensions and LAN devices to reach this agent.
    Chrome 94+ blocks Private Network Access without this header."""
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            from fastapi.responses import Response as _Resp
            r = _Resp()
            r.headers["Access-Control-Allow-Origin"]          = "*"
            r.headers["Access-Control-Allow-Methods"]         = "*"
            r.headers["Access-Control-Allow-Headers"]         = "*"
            r.headers["Access-Control-Allow-Private-Network"] = "true"
            return r
        response = await call_next(request)
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response


app.add_middleware(_PrivateNetworkMiddleware)

# ── WebSocket ─────────────────────────────────────────────────────────────────

async def _broadcast(payload: dict):
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
    await ws.send_text(json.dumps({"type": "snapshot", "data": queue.list_all()}))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _ws_clients.discard(ws)


# ── Web UI ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    """Serve the web UI — accessible from any device on the network."""
    index = WEB_DIR / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text(encoding="utf-8"))
    # Minimal fallback if web/index.html hasn't been placed yet
    return HTMLResponse(content="""<!DOCTYPE html>
<html><head><title>Fetchr</title>
<style>body{font-family:sans-serif;background:#0f0f0f;color:#f0f0f0;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}
.box{text-align:center;} a{color:#5865f2;}</style></head>
<body><div class="box">
<h1>⬇ Fetchr Agent v2.0</h1>
<p>Agent is running. Place <code>web/index.html</code> to enable the full UI.</p>
<p><a href="/docs">API docs →</a></p>
</div></body></html>""")


# ── Settings ──────────────────────────────────────────────────────────────────

SETTINGS_FILE = Path(__file__).parent / "settings.json"

_settings = {
    "save_path":            DEFAULT_SAVE_PATH,
    "max_concurrent":       3,
    "max_speed_kbps":       0,
    "intercept_all":        True,
    "cookies_browser":      "chrome",
    "hash_check":           False,
    "auto_extract":         False,
    "organise_by_type":     False,
    "notification_url":     "",           # NEW: Apprise URL (ntfy, Gotify, Telegram…)
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
    save_path:          Optional[str]  = None
    max_concurrent:     Optional[int]  = None
    max_speed_kbps:     Optional[int]  = None
    intercept_all:      Optional[bool] = None
    cookies_browser:    Optional[str]  = None
    hash_check:         Optional[bool] = None
    auto_extract:       Optional[bool] = None
    organise_by_type:   Optional[bool] = None
    notification_url:   Optional[str]  = None


@app.put("/settings")
async def update_settings(body: SettingsUpdate):
    if body.save_path         is not None: _settings["save_path"]         = body.save_path
    if body.max_concurrent    is not None:
        _settings["max_concurrent"] = body.max_concurrent
        queue.max_concurrent         = body.max_concurrent
    if body.max_speed_kbps    is not None: _settings["max_speed_kbps"]    = body.max_speed_kbps
    if body.intercept_all     is not None: _settings["intercept_all"]     = body.intercept_all
    if body.cookies_browser   is not None: _settings["cookies_browser"]   = body.cookies_browser
    if body.hash_check        is not None: _settings["hash_check"]        = body.hash_check
    if body.auto_extract      is not None: _settings["auto_extract"]      = body.auto_extract
    if body.organise_by_type  is not None: _settings["organise_by_type"]  = body.organise_by_type
    if body.notification_url  is not None:
        _settings["notification_url"] = body.notification_url
        notifications.configure(body.notification_url)   # apply immediately
    _save_settings()
    return _settings


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    active = sum(
        1 for d in queue.list_all()
        if d["status"] in (DownloadStatus.ACTIVE, DownloadStatus.QUEUED)
    )
    return {
        "status":  "ok",
        "version": "2.0.0",
        "active":  active,
        "queued":  sum(1 for d in queue.list_all() if d["status"] == DownloadStatus.QUEUED),
    }


# ── Downloads CRUD ────────────────────────────────────────────────────────────

class AddDownloadRequest(BaseModel):
    url:       str
    filename:  Optional[str] = None
    save_path: Optional[str] = None
    dl_type:   Optional[str] = "direct"
    referer:   Optional[str] = None
    cookies:   Optional[str] = None


def _guess_filename(url: str) -> str:
    path = url.split("?")[0].rstrip("/")
    name = path.split("/")[-1]
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name or "download"


@app.post("/downloads", status_code=201)
async def add_download(body: AddDownloadRequest):
    filename  = body.filename  or _guess_filename(body.url)
    save_path = body.save_path or _settings["save_path"]
    dl_type   = DownloadType(body.dl_type) if body.dl_type else DownloadType.DIRECT

    item = await queue.add(
        url       = body.url,
        filename  = filename,
        save_path = save_path,
        dl_type   = dl_type,
        referer   = body.referer or "",
        cookies   = body.cookies or "",
    )
    if item is None:
        # Already downloaded — return a meaningful response
        return {"skipped": True, "reason": "URL already in download history"}
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


# ── Batch download ────────────────────────────────────────────────────────────

class BatchDownloadRequest(BaseModel):
    items: list      # list of {url, filename?, save_path?, dl_type?, referer?, cookies?}


@app.post("/downloads/batch", status_code=201)
async def batch_download(body: BatchDownloadRequest):
    added = 0
    skipped = 0
    for entry in body.items:
        url       = entry.get("url", "")
        filename  = entry.get("filename") or _guess_filename(url)
        save_path = entry.get("save_path") or _settings["save_path"]
        dl_type   = DownloadType(entry.get("dl_type", "direct"))
        item = await queue.add(
            url       = url,
            filename  = filename,
            save_path = save_path,
            dl_type   = dl_type,
            referer   = entry.get("referer", ""),
            cookies   = entry.get("cookies", ""),
        )
        if item is None:
            skipped += 1
        else:
            added += 1
    return {"added": added, "skipped": skipped}


# ── Scraper (link grabber) ────────────────────────────────────────────────────

class ScrapeRequest(BaseModel):
    url:     str
    cookies: Optional[str] = ""


@app.post("/scrape")
async def scrape_url(body: ScrapeRequest):
    """
    Gallery/batch link grabber — inspired by CyberDropDownloader's crawlers.
    Detects the site, scrapes all downloadable items, returns them for the
    user to review before downloading.
    """
    from scrapers import scrape, find_scraper

    scraper = find_scraper(body.url)
    if not scraper:
        # No dedicated scraper — try yt-dlp media scan as fallback
        from downloader import scan_media
        formats = await scan_media(body.url)
        return {
            "url":      body.url,
            "scraper":  "yt-dlp",
            "count":    len(formats),
            "items":    [],
            "formats":  formats,
        }

    items = await scraper.scrape(body.url, cookies=body.cookies or "")
    return {
        "url":     body.url,
        "scraper": type(scraper).__name__,
        "count":   len(items),
        "items":   [
            {
                "url":      i.url,
                "filename": i.filename,
                "referer":  i.referer,
                "album":    i.album,
                "dl_type":  i.dl_type,
            }
            for i in items
        ],
    }


# ── Media scanner ─────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    url: str


@app.post("/scan")
async def scan_url(body: ScanRequest):
    from downloader import scan_media
    formats = await scan_media(body.url)
    return {"url": body.url, "formats": formats}


# ── History ───────────────────────────────────────────────────────────────────

@app.get("/history")
async def get_history(limit: int = 500):
    return await db.get_history(limit=limit)


@app.delete("/history")
async def clear_history():
    """Wipe the entire download history (dedup list resets)."""
    import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute("DELETE FROM history")
        await conn.commit()
    return {"ok": True}


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host      = "0.0.0.0",    # listen on all interfaces — web UI reachable from LAN
        port      = PORT,
        log_level = "info",
    )
