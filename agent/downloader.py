"""
Fetchr Download Engine
──────────────────────────────────────────────────────────────────────────────
Architecture
  • aria2c (C++ binary) — primary engine for direct files
      - 16 parallel connections per file (IDM-style multi-segment)
      - Writes to filename.part, renames to filename on completion
  • yt-dlp — URL extraction for media/streaming platforms
      - Extracts real URL + auth headers, hands off to aria2c
      - Falls back to yt-dlp native download if aria2c unavailable
  • ffmpeg — HLS/DASH stream muxing
  • aiohttp — last-resort fallback when aria2c is not installed

New in v2:
  • .part file convention — incomplete files never look complete
  • Retry with exponential backoff (up to 5 attempts)
  • Per-domain rate limiting via rate_limiter.domain_limiter
  • Rate-limit-aware aiohttp fallback

Install:
  macOS:   brew install aria2 ffmpeg
  Ubuntu:  apt install aria2 ffmpeg
  Windows: winget install aria2
──────────────────────────────────────────────────────────────────────────────
"""

import asyncio
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Awaitable

import aiohttp

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False

from queue_manager import DownloadItem, DownloadStatus
from rate_limiter import domain_limiter

# ── Tuning ───────────────────────────────────────────────────────────────────
ARIA2C_CONNECTIONS = 16
ARIA2C_MIN_SPLIT   = "1M"
CHUNK_SIZE         = 256 * 1024
CONNECT_TIMEOUT    = 30
READ_TIMEOUT       = 60
SPEED_WINDOW       = 2.0
MAX_RETRIES        = 5
RETRY_BACKOFF_BASE = 2        # seconds — doubles per attempt: 2,4,8,16,32

_BASE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_SESSION_HEADERS = {"User-Agent": _BASE_UA}

# ── Binary detection ─────────────────────────────────────────────────────────
ARIA2C_BIN = shutil.which("aria2c")
FFMPEG_BIN = shutil.which("ffmpeg")

if ARIA2C_BIN:
    print(f"✅  aria2c found: {ARIA2C_BIN}  (16-connection mode active)")
else:
    print("⚠️   aria2c not found — falling back to aiohttp multi-segment")

if FFMPEG_BIN:
    print(f"✅  ffmpeg found: {FFMPEG_BIN}  (HLS/m3u8 merging enabled)")
else:
    print("⚠️   ffmpeg not found — HLS streams and video+audio merging unavailable")

HLS_RE       = re.compile(r"\.(m3u8|m3u|mpd)(\?|#|$)", re.IGNORECASE)
_PAGE_EXTS   = re.compile(r"\.(html?|php|asp|aspx|jsp|cfm|cgi|shtml)(\?|#|$)", re.IGNORECASE)
_DIRECT_EXTS = re.compile(
    r"\.(mp4|mkv|webm|avi|mov|flv|wmv|m4v|ts|"
    r"mp3|m4a|flac|wav|ogg|opus|aac|wma|"
    r"zip|rar|7z|tar|gz|bz2|xz|iso|"
    r"exe|msi|dmg|pkg|deb|rpm|apk|ipa|"
    r"pdf|docx?|xlsx?|pptx?|"
    r"jpg|jpeg|png|gif|webp|svg|bmp)(\?|#|$)",
    re.IGNORECASE,
)


# ── Header helpers ────────────────────────────────────────────────────────────

def _build_headers(item: DownloadItem) -> dict:
    h = {**_SESSION_HEADERS}
    if item.referer:
        h["Referer"] = item.referer
    if item.cookies:
        h["Cookie"] = item.cookies
    return h


# ── URL classification ────────────────────────────────────────────────────────

def _classify_url(url: str) -> str:
    """Auto-detect URL type regardless of what dl_type the extension sent."""
    try:
        path = url.split("?")[0].split("#")[0]
    except Exception:
        return "direct"
    if HLS_RE.search(path):
        return "hls"
    if _DIRECT_EXTS.search(path):
        return "direct"
    return "media"


# ── Main entry point with retry ──────────────────────────────────────────────

async def run_download(
    item: DownloadItem,
    notify: Callable[[DownloadItem], Awaitable[None]],
):
    """
    Retry wrapper around _do_download.
    On transient failure: exponential backoff up to MAX_RETRIES attempts.
    On cancellation or unrecoverable error: stop immediately.
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            await _do_download(item, notify)
            return
        except asyncio.CancelledError:
            item.status = DownloadStatus.CANCELLED
            await notify(item)
            return
        except Exception as e:
            err_str = str(e)[:300]
            # Don't retry on permanent errors
            if any(tag in err_str for tag in [
                "code 3",   # resource not found
                "code 9",   # disk full
                "code 19",  # DNS fail
                "404", "Not Found",
            ]):
                item.status = DownloadStatus.ERROR
                item.error  = err_str
                print(f"❌  Download failed [{item.filename}]: {e}")
                await notify(item)
                return

            if attempt == MAX_RETRIES:
                item.status = DownloadStatus.ERROR
                item.error  = err_str
                print(f"❌  Download failed after {MAX_RETRIES} retries [{item.filename}]: {e}")
                await notify(item)
                return

            wait = RETRY_BACKOFF_BASE ** (attempt + 1)
            print(f"⚠️   Attempt {attempt + 1} failed ({e}) — retrying in {wait}s")
            item.error    = f"Retrying… (attempt {attempt + 1}/{MAX_RETRIES})"
            item.progress = 0.0
            item.speed    = 0.0
            await notify(item)
            await asyncio.sleep(wait)
            item.error = None


async def _do_download(
    item: DownloadItem,
    notify: Callable[[DownloadItem], Awaitable[None]],
):
    """Core download dispatch — routes to the right engine."""
    auto = _classify_url(item.url)
    if auto == "hls":
        item.dl_type = "media"
    elif auto == "direct" and item.dl_type == "media":
        item.dl_type = "direct"

    if item.dl_type == "media":
        await _download_media(item, notify)
    else:
        await _download_direct(item, notify)

    if item.status not in (DownloadStatus.CANCELLED, DownloadStatus.ERROR):
        item.status      = DownloadStatus.COMPLETE
        item.progress    = 100.0
        item.speed       = 0.0
        item.eta         = 0
        item.finished_at = datetime.utcnow().isoformat()

    await notify(item)


# ── Direct download dispatcher ───────────────────────────────────────────────

async def _download_direct(item: DownloadItem, notify):
    if ARIA2C_BIN:
        try:
            await _aria2c_download(
                item, notify,
                url      = item.url,
                filename = item.filename,
                dest_dir = item.save_path,
            )
            return
        except RuntimeError as e:
            code = str(e)
            if any(f"code {c}" in code for c in ["3", "9", "16", "17", "18", "19"]):
                raise
            print(f"⚠️   aria2c failed ({e}), trying yt-dlp…")

    if YTDLP_AVAILABLE and not _DIRECT_EXTS.search(item.url.split("?")[0]):
        item.dl_type = "media"
        await _download_media(item, notify)
        return

    await _aiohttp_download(item, notify)


# ══════════════════════════════════════════════════════════════════════════════
#  aria2c core
# ══════════════════════════════════════════════════════════════════════════════

async def _aria2c_download(item, notify, url, filename, dest_dir, extra_args=None):
    """
    Run aria2c as an async subprocess with real-time progress parsing.
    Writes to filename.part — renames to filename on clean exit.
    """
    dest_dir   = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    part_name  = filename + ".part"
    final_path = dest_dir / filename
    part_path  = dest_dir / part_name

    cmd = [
        ARIA2C_BIN,
        "--dir",          str(dest_dir),
        "--out",          part_name,                      # write to .part
        "--max-connection-per-server", str(ARIA2C_CONNECTIONS),
        "--split",                     str(ARIA2C_CONNECTIONS),
        "--min-split-size",            ARIA2C_MIN_SPLIT,
        "--file-allocation=none",
        "--auto-file-renaming=false",
        "--allow-overwrite=true",
        "--retry-wait=3",
        "--max-tries=8",
        "--connect-timeout=15",
        "--timeout=60",
        "--show-console-readout=true",
        "--summary-interval=1",
        f"--user-agent={_BASE_UA}",
    ]

    if item.referer:
        cmd += [f"--referer={item.referer}"]
    if item.cookies:
        cmd += [f"--header=Cookie: {item.cookies}"]
    if extra_args:
        cmd += extra_args

    cmd.append(url)
    item.segments = ARIA2C_CONNECTIONS

    # Rate-limit: one token per download start (not per segment)
    await domain_limiter.acquire(url)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    try:
        async for raw_line in proc.stdout:
            if item.status == DownloadStatus.CANCELLED:
                proc.kill()
                break
            if item.status == DownloadStatus.PAUSED:
                await item._pause_event.wait()

            line = raw_line.decode("utf-8", errors="replace")
            _parse_aria2c_progress(line, item)
            await notify(item)
    except asyncio.CancelledError:
        proc.kill()
        raise

    await proc.wait()

    if proc.returncode not in (0, None) and item.status not in (
        DownloadStatus.CANCELLED, DownloadStatus.ERROR
    ):
        raise RuntimeError(f"aria2c exited with code {proc.returncode}")

    # Rename .part → final filename on success
    if part_path.exists() and item.status not in (DownloadStatus.CANCELLED, DownloadStatus.ERROR):
        part_path.rename(final_path)
        item.filename = filename


# aria2c progress line: [#abc123 12.5MiB/100MiB(12%) CN:16 DL:8.5MiB ETA:10s]
_ARIA2_PROGRESS_RE = re.compile(
    r"\[#\w+\s+"
    r"([\d.]+\s*\w+)/([\d.]+\s*\w+)\((\d+)%\)"
    r"(?:.*?DL:([\d.]+\s*\w+))?"
    r"(?:.*?ETA:([\dhms]+))?"
)


def _parse_aria2c_progress(line: str, item: DownloadItem):
    m = _ARIA2_PROGRESS_RE.search(line)
    if not m:
        return
    downloaded_str, total_str, pct, speed_str, eta_str = m.groups()
    item.progress   = float(pct)
    item.downloaded = _parse_aria2c_bytes(downloaded_str)
    item.total      = _parse_aria2c_bytes(total_str)
    item.speed      = _parse_aria2c_bytes(speed_str) if speed_str else 0.0
    if eta_str:
        item.eta = _parse_eta(eta_str)


def _parse_aria2c_bytes(s: str) -> int:
    if not s:
        return 0
    s = s.strip().replace(" ", "")
    units = {"GiB": 1024**3, "MiB": 1024**2, "KiB": 1024,
             "GB": 1000**3, "MB": 1000**2, "KB": 1000, "B": 1}
    for unit, mult in units.items():
        if s.endswith(unit):
            try:
                return int(float(s[: -len(unit)]) * mult)
            except ValueError:
                return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _parse_eta(s: str) -> int:
    total = 0
    for val, unit in re.findall(r"(\d+)([hms])", s):
        total += int(val) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


# ══════════════════════════════════════════════════════════════════════════════
#  Media download (yt-dlp + aria2c)
# ══════════════════════════════════════════════════════════════════════════════

async def _download_media(item: DownloadItem, notify):
    dest_dir = Path(item.save_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_event_loop()

    # HLS/DASH → ffmpeg
    if HLS_RE.search(item.url):
        if FFMPEG_BIN:
            await _ffmpeg_hls_download(item, notify, dest_dir)
            return
        elif YTDLP_AVAILABLE:
            await _media_ytdlp_native(item, notify, loop, dest_dir)
            return
        else:
            raise RuntimeError("HLS stream detected but neither ffmpeg nor yt-dlp is installed")

    if not YTDLP_AVAILABLE:
        await _download_direct(item, notify)
        return

    try:
        if ARIA2C_BIN:
            await _media_via_aria2c(item, notify, loop, dest_dir)
        else:
            await _media_ytdlp_native(item, notify, loop, dest_dir)
    except Exception as yt_err:
        print(f"⚠️   yt-dlp failed ({yt_err}), retrying as direct download…")
        item.error = None
        await _download_direct(item, notify)


async def _ffmpeg_hls_download(item, notify, dest_dir):
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    title = item.filename or "video"
    if not title.endswith(".mp4"):
        title = re.sub(r"\.[^.]+$", "", title) + ".mp4"
    title = re.sub(r'[\\/*?:"<>|]', "_", title).strip()

    part_path  = dest_dir / (title + ".part")
    final_path = dest_dir / title
    item.filename = title
    await notify(item)

    headers = f"User-Agent: {_BASE_UA}\r\n"
    if item.referer:
        headers += f"Referer: {item.referer}\r\n"
    if item.cookies:
        headers += f"Cookie: {item.cookies}\r\n"

    cmd = [
        FFMPEG_BIN, "-y",
        "-headers", headers,
        "-i", item.url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-loglevel", "error",
        str(part_path),
    ]

    item.segments = 1
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    _FFMPEG_KV = re.compile(r"^(\w+)=(.+)$")
    kv: dict = {}

    async def _read_progress():
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            m = _FFMPEG_KV.match(line)
            if m:
                kv[m.group(1)] = m.group(2)
            if "total_size" in kv:
                item.downloaded = int(kv.get("total_size", 0) or 0)
                item.total      = 0
                item.progress   = min(99.0, item.progress + 0.3)
                if item.status == DownloadStatus.CANCELLED:
                    proc.kill()
                    return
                await notify(item)

    try:
        await asyncio.wait_for(_read_progress(), timeout=7200)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("ffmpeg download timed out after 2 hours")
    except asyncio.CancelledError:
        proc.kill()
        raise

    await proc.wait()

    if proc.returncode != 0 and item.status not in (DownloadStatus.CANCELLED,):
        stderr = (await proc.stderr.read()).decode("utf-8", errors="replace")
        raise RuntimeError(f"ffmpeg failed (code {proc.returncode}): {stderr[:300]}")

    # Rename .part → final
    if part_path.exists():
        part_path.rename(final_path)
    item.progress   = 100.0
    item.downloaded = final_path.stat().st_size if final_path.exists() else item.downloaded


async def _media_via_aria2c(item, notify, loop, dest_dir):
    def _extract():
        opts = {
            "quiet":    True,
            "no_warnings": True,
            "cookiesfrombrowser": ("chrome",),
            "format":   "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(item.url, download=False)

    info = await loop.run_in_executor(None, _extract)
    if not info:
        raise RuntimeError("yt-dlp could not extract info for this URL")

    entry = info
    if "entries" in info and info.get("entries"):
        entry = info["entries"][0]
        if entry is None:
            raise RuntimeError("yt-dlp returned an empty playlist entry")

    title = re.sub(
        r'[\\/*?:"<>|]',
        "_",
        (entry.get("title") or entry.get("id") or "video").strip()
    )
    item.filename = f"{title}.mp4"
    await notify(item)

    urls_to_download = []
    if "requested_formats" in entry:
        for fmt in entry["requested_formats"]:
            urls_to_download.append({
                "url": fmt["url"], "ext": fmt.get("ext", "mp4"),
                "http_headers": fmt.get("http_headers", {}),
            })
    elif entry.get("url"):
        urls_to_download.append({
            "url": entry["url"], "ext": entry.get("ext", "mp4"),
            "http_headers": entry.get("http_headers", {}),
        })
    elif entry.get("formats"):
        formats = entry["formats"]
        best = None
        for fmt in reversed(formats):
            if (fmt.get("url")
                    and fmt.get("vcodec", "none") != "none"
                    and fmt.get("acodec", "none") != "none"):
                best = fmt
                break
        if not best:
            for fmt in reversed(formats):
                if fmt.get("url"):
                    best = fmt
                    break
        if not best:
            raise RuntimeError("yt-dlp found no downloadable format")
        urls_to_download.append({
            "url": best["url"], "ext": best.get("ext", "mp4"),
            "http_headers": best.get("http_headers", {}),
        })
    else:
        raise RuntimeError("yt-dlp could not find a download URL")

    if len(urls_to_download) == 1:
        fmt      = urls_to_download[0]
        filename = f"{title}.{fmt['ext']}"
        extra    = [f"--header={k}: {v}"
                    for k, v in fmt.get("http_headers", {}).items()
                    if k.lower() != "user-agent"]
        item.filename = filename
        await _aria2c_download(item, notify, fmt["url"], filename, str(dest_dir), extra)
    else:
        parts = []
        for i, fmt in enumerate(urls_to_download):
            part_name = f"{title}.part{i}.{fmt['ext']}"
            parts.append(str(dest_dir / part_name))
            extra = [f"--header={k}: {v}"
                     for k, v in fmt.get("http_headers", {}).items()
                     if k.lower() != "user-agent"]
            shadow = _shadow_item(item, part_name)
            await _aria2c_download(shadow, notify, fmt["url"], part_name,
                                   str(dest_dir), extra)
            item.downloaded = shadow.downloaded
            item.total      = shadow.total
            item.progress   = shadow.progress

        out_file      = str(dest_dir / f"{title}.mp4")
        item.filename = f"{title}.mp4"
        if FFMPEG_BIN and len(parts) == 2:
            merge_cmd = [FFMPEG_BIN, "-y",
                         "-i", parts[0], "-i", parts[1],
                         "-c", "copy", out_file]
            proc = await asyncio.create_subprocess_exec(
                *merge_cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            for p in parts:
                Path(p).unlink(missing_ok=True)
        else:
            Path(parts[0]).rename(out_file)
            for p in parts[1:]:
                Path(p).unlink(missing_ok=True)


async def _media_ytdlp_native(item, notify, loop, dest_dir):
    def _get_title():
        try:
            with yt_dlp.YoutubeDL({
                "quiet": True, "no_warnings": True,
                "cookiesfrombrowser": ("chrome",),
            }) as ydl:
                info = ydl.extract_info(item.url, download=False)
                return info.get("title") or info.get("id") or "video"
        except Exception:
            return None

    title = await loop.run_in_executor(None, _get_title)
    if title:
        item.filename = f"{title}.mp4"
        await notify(item)

    def _progress_hook(d):
        if d["status"] == "downloading":
            item.downloaded = d.get("downloaded_bytes", 0) or 0
            item.total      = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            item.speed      = d.get("speed") or 0.0
            item.eta        = d.get("eta") or 0
            if item.total:
                item.progress = (item.downloaded / item.total) * 100
            asyncio.run_coroutine_threadsafe(notify(item), loop)
        elif d["status"] == "finished":
            item.progress = 100.0
            item.speed    = 0.0
            asyncio.run_coroutine_threadsafe(notify(item), loop)

    ydl_opts = {
        "outtmpl":              str(dest_dir / "%(title)s.%(ext)s"),
        "progress_hooks":       [_progress_hook],
        "quiet":                True,
        "no_warnings":          True,
        "format":               "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format":  "mp4",
        "noplaylist":           True,
        "cookiesfrombrowser":   ("chrome",),
        "retries":              8,
        "fragment_retries":     8,
        "extractor_retries":    4,
        "sleep_interval_requests": 1,
        "sleep_interval":       2,
        "max_sleep_interval":   5,
    }
    await loop.run_in_executor(None, _ytdlp_run, item.url, ydl_opts)


def _ytdlp_run(url, opts):
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


def _shadow_item(item: DownloadItem, new_filename: str) -> DownloadItem:
    from dataclasses import replace
    shadow = replace(item, filename=new_filename, downloaded=0, total=0, progress=0.0)
    shadow._pause_event = item._pause_event
    return shadow


# ══════════════════════════════════════════════════════════════════════════════
#  aiohttp fallback (no aria2c)
# ══════════════════════════════════════════════════════════════════════════════

async def _aiohttp_download(item: DownloadItem, notify):
    """Single-stream aiohttp download — used only when aria2c is not installed."""
    dest_dir = Path(item.save_path)
    dest_dir.mkdir(parents=True, exist_ok=True)

    final_path = dest_dir / item.filename
    part_path  = dest_dir / (item.filename + ".part")

    headers    = dict(_build_headers(item))
    resume_pos = 0

    # Resume from existing .part file
    if part_path.exists():
        resume_pos = part_path.stat().st_size
        if resume_pos > 0:
            headers["Range"] = f"bytes={resume_pos}-"

    timeout = aiohttp.ClientTimeout(connect=CONNECT_TIMEOUT, sock_read=READ_TIMEOUT)

    await domain_limiter.acquire(item.url)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(item.url, headers=headers, allow_redirects=True) as resp:
            if resp.status in (401, 403) and YTDLP_AVAILABLE:
                item.dl_type = "media"
                await _download_media(item, notify)
                return
            resp.raise_for_status()

            cr = resp.headers.get("Content-Range", "")
            cl = resp.headers.get("Content-Length")
            if cr and "/" in cr:
                item.total = int(cr.split("/")[-1])
            elif cl:
                item.total = int(cl) + resume_pos

            item.downloaded = resume_pos
            tracker         = _SpeedTracker()
            last_notify     = [0.0]

            mode = "ab" if resume_pos else "wb"
            with open(part_path, mode) as f:
                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    await item._pause_event.wait()
                    if item.status == DownloadStatus.CANCELLED:
                        raise asyncio.CancelledError()
                    f.write(chunk)
                    item.downloaded += len(chunk)
                    tracker.add(len(chunk))
                    item.speed = tracker.speed()
                    if item.total:
                        item.progress = (item.downloaded / item.total) * 100
                        item.eta = int((item.total - item.downloaded) / item.speed) \
                                   if item.speed > 1 else 0
                    now = time.monotonic()
                    if now - last_notify[0] >= 0.15:
                        last_notify[0] = now
                        await notify(item)

    # Rename .part → final on clean completion
    if part_path.exists():
        part_path.rename(final_path)


# ── Media scanner ─────────────────────────────────────────────────────────────

async def scan_media(url: str) -> list:
    if not YTDLP_AVAILABLE:
        return []
    loop = asyncio.get_event_loop()

    def _extract():
        with yt_dlp.YoutubeDL({
            "quiet": True, "no_warnings": True,
            "cookiesfrombrowser": ("chrome",),
        }) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return []
            return [
                {
                    "format_id":  f.get("format_id"),
                    "ext":        f.get("ext"),
                    "resolution": f.get("resolution") or f.get("format_note", ""),
                    "filesize":   f.get("filesize") or f.get("filesize_approx"),
                    "vcodec":     f.get("vcodec"),
                    "acodec":     f.get("acodec"),
                }
                for f in info.get("formats", [])
            ]

    try:
        return await loop.run_in_executor(None, _extract)
    except Exception:
        return []


# ── Speed tracker ──────────────────────────────────────────────────────────────

class _SpeedTracker:
    def __init__(self, window=SPEED_WINDOW):
        self._window  = window
        self._samples = []

    def add(self, b):
        now = time.monotonic()
        self._samples.append((now, b))
        cutoff = now - self._window
        self._samples = [(t, x) for t, x in self._samples if t >= cutoff]

    def speed(self):
        if not self._samples:
            return 0.0
        total = sum(x for _, x in self._samples)
        if len(self._samples) < 2:
            return total / self._window
        elapsed = self._samples[-1][0] - self._samples[0][0]
        return total / elapsed if elapsed > 0 else 0.0
