"""
Fetchr Download Engine — aria2c powered
─────────────────────────────────────────────────────────────────────────────
Architecture
  • aria2c (C++ binary) is the ACTUAL downloader for everything
      - Direct files  → aria2c with up to 16 parallel connections per file
      - Media/streams → yt-dlp extracts the real URL(s) + auth,
                        then aria2c downloads with 16 connections
  • yt-dlp is used ONLY for:
      - extracting video URLs from platforms (YouTube, EroMe, etc.)
      - passing cookies / auth tokens into aria2c headers
  • aiohttp is kept as last-resort fallback if aria2c is not installed

Why aria2c?
  IDM's multi-segment trick is exactly what aria2c does natively,
  in C++, with battle-tested retry logic. Saturates bandwidth immediately.

Install aria2c:
  macOS:   brew install aria2
  Ubuntu:  apt install aria2
  Windows: winget install aria2  (or scoop install aria2)
─────────────────────────────────────────────────────────────────────────────
"""

import asyncio
import re
import shutil
import time
from pathlib import Path
from typing import Callable, Awaitable

import aiohttp

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False

from queue_manager import DownloadItem, DownloadStatus

# ── Tuning ─────────────────────────────────────────────────────────────────
ARIA2C_CONNECTIONS = 16       # connections per server (like IDM's 16-segment mode)
ARIA2C_MIN_SPLIT   = "1M"    # minimum split size
CHUNK_SIZE         = 256 * 1024
CONNECT_TIMEOUT    = 30
READ_TIMEOUT       = 60
SPEED_WINDOW       = 2.0

_BASE_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)
_SESSION_HEADERS = {"User-Agent": _BASE_UA}

# Detect aria2c once at import time
ARIA2C_BIN = shutil.which("aria2c")

if ARIA2C_BIN:
    print(f"✅  aria2c found: {ARIA2C_BIN}  (16-connection mode active)")
else:
    print("⚠️   aria2c not found — falling back to aiohttp multi-segment")
    print("     Install: brew install aria2  |  apt install aria2")


def _build_headers(item: DownloadItem) -> dict:
    h = {**_SESSION_HEADERS}
    if item.referer:
        h["Referer"] = item.referer
    if item.cookies:
        h["Cookie"] = item.cookies
    return h


FFMPEG_BIN = shutil.which("ffmpeg")

if FFMPEG_BIN:
    print(f"✅  ffmpeg found: {FFMPEG_BIN}  (HLS/m3u8 merging enabled)")
else:
    print("⚠️   ffmpeg not found — HLS streams and video+audio merging will be unavailable")

HLS_RE = re.compile(r"\.(m3u8|m3u|mpd)(\?|#|$)", re.IGNORECASE)


# ── URL type detection ───────────────────────────────────────────────────────

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

def _classify_url(url: str) -> str:
    """
    Auto-classify a URL regardless of what dl_type the extension sent.
    Returns: 'hls' | 'direct' | 'media'
    """
    try:
        path = url.split("?")[0].split("#")[0]
    except Exception:
        return "direct"

    if HLS_RE.search(path):
        return "hls"
    if _DIRECT_EXTS.search(path):
        return "direct"
    # Page URL or unknown → try yt-dlp
    return "media"


# ── Entry point ─────────────────────────────────────────────────────────────

async def run_download(item: DownloadItem, notify: Callable[[DownloadItem], Awaitable[None]]):
    """
    Robust download entry point with automatic fallback chain:
      HLS/m3u8   → ffmpeg → yt-dlp-native
      Direct file → aria2c → aiohttp
      Media page  → yt-dlp+aria2c → yt-dlp-native → direct → aiohttp
      Any 403/401 → retry with yt-dlp cookie extraction
    """
    try:
        # Override dl_type with auto-detection when URL makes it obvious
        auto = _classify_url(item.url)
        if auto == "hls":
            item.dl_type = "media"  # forces HLS path in _download_media
        elif auto == "direct" and item.dl_type == "media":
            # Extension said "media" but URL is a plain file — download directly (faster)
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
            from datetime import datetime
            item.finished_at = datetime.utcnow().isoformat()

    except asyncio.CancelledError:
        item.status = DownloadStatus.CANCELLED
    except Exception as e:
        item.status = DownloadStatus.ERROR
        item.error  = str(e)[:300]
        print(f"❌  Download failed [{item.filename}]: {e}")
    finally:
        await notify(item)


# ── Direct download dispatcher ──────────────────────────────────────────────

async def _download_direct(item: DownloadItem, notify):
    """
    Route direct file downloads:
      aria2c (16 connections) → aiohttp on failure
    On 403/401 automatically retries through yt-dlp.
    """
    if ARIA2C_BIN:
        try:
            await _aria2c_download(
                item, notify,
                url=item.url,
                filename=item.filename,
                dest_dir=item.save_path,
            )
            return
        except RuntimeError as e:
            code = str(e)
            # aria2c code 3 = resource not found, 22/24 = connection reset,
            # 9 = not enough disk, 19 = DNS fail — don't retry these with yt-dlp
            if any(f"code {c}" in code for c in ["3", "9", "16", "17", "18", "19"]):
                raise
            # For 403-equivalent or unknown errors, fall through to yt-dlp
            print(f"⚠  aria2c failed ({e}), trying yt-dlp…")

    # If the URL looks like a media page (no direct extension), route to media handler
    if YTDLP_AVAILABLE and not _DIRECT_EXTS.search(item.url.split("?")[0]):
        item.dl_type = "media"
        await _download_media(item, notify)
        return

    # Last resort: aiohttp single-stream
    await _aiohttp_download(item, notify)


# ═══════════════════════════════════════════════════════════════════════════ #
#   aria2c core                                                               #
# ═══════════════════════════════════════════════════════════════════════════ #

async def _aria2c_download(item, notify, url, filename, dest_dir, extra_args=None):
    """
    Run aria2c as an async subprocess with real-time progress parsing.
    16 connections per server — IDM-style multi-segment in one command.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Build aria2c command
    cmd = [
        ARIA2C_BIN,
        "--dir",          str(dest_dir),
        "--out",          filename,
        # Multi-connection
        "--max-connection-per-server", str(ARIA2C_CONNECTIONS),
        "--split",                     str(ARIA2C_CONNECTIONS),
        "--min-split-size",            ARIA2C_MIN_SPLIT,
        # Reliability
        "--file-allocation=none",
        "--auto-file-renaming=false",
        "--allow-overwrite=true",
        "--retry-wait=3",
        "--max-tries=8",
        "--connect-timeout=15",
        "--timeout=60",
        # Progress (machine-readable)
        "--show-console-readout=true",
        "--summary-interval=1",
        # Headers
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

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    # Stream and parse output
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


# aria2c progress line examples:
# [#abc123 12.5MiB/100MiB(12%) CN:16 DL:8.5MiB ETA:10s]
# [#abc123 100MiB/100MiB(100%) CN:0 DL:0B]

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
    """Convert aria2c human-readable size to bytes. e.g. '12.5MiB' → int"""
    if not s:
        return 0
    s = s.strip().replace(" ", "")
    units = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3,
             "KB": 1000, "MB": 1000**2, "GB": 1000**3}
    for unit, mult in sorted(units.items(), key=lambda x: -len(x[0])):
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
    """Convert aria2c ETA string to seconds. '1m23s' → 83"""
    total = 0
    for val, unit in re.findall(r"(\d+)([hms])", s):
        total += int(val) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


# ═══════════════════════════════════════════════════════════════════════════ #
#   Media download (yt-dlp for URL extraction + aria2c for transfer)          #
# ═══════════════════════════════════════════════════════════════════════════ #

async def _download_media(item: DownloadItem, notify):
    dest_dir = Path(item.save_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_event_loop()

    # ── m3u8 / HLS / DASH → ffmpeg is the right tool ───────────────────────
    if HLS_RE.search(item.url):
        if FFMPEG_BIN:
            await _ffmpeg_hls_download(item, notify, dest_dir)
            return
        elif YTDLP_AVAILABLE:
            # ffmpeg not found — yt-dlp can still handle HLS via its internal downloader
            await _media_ytdlp_native(item, notify, loop, dest_dir)
            return
        else:
            raise RuntimeError("HLS stream detected but neither ffmpeg nor yt-dlp is installed")

    if not YTDLP_AVAILABLE:
        # No yt-dlp — treat as a direct download and hope for the best
        await _download_direct(item, notify)
        return

    try:
        # ── If aria2c is available: extract URL with yt-dlp, download with aria2c ──
        if ARIA2C_BIN:
            await _media_via_aria2c(item, notify, loop, dest_dir)
        else:
            await _media_ytdlp_native(item, notify, loop, dest_dir)
    except Exception as yt_err:
        # yt-dlp failed (unsupported site, geo-block, etc.)
        # Fall back to treating the URL as a plain direct download
        print(f"⚠  yt-dlp failed ({yt_err}), retrying as direct download…")
        item.error = None
        await _download_direct(item, notify)


async def _ffmpeg_hls_download(item, notify, dest_dir):
    """
    Download an HLS (m3u8) or DASH stream using ffmpeg.
    ffmpeg handles segment fetching, authentication, and muxing natively.
    Progress is estimated via ffmpeg's time= output lines.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Build a clean filename
    title = item.filename or "video"
    if not title.endswith(".mp4"):
        title = re.sub(r"\.[^.]+$", "", title) + ".mp4"
    title = re.sub(r'[\\/*?:"<>|]', "_", title).strip()
    item.filename = title
    await notify(item)

    out_path = str(dest_dir / title)

    # Build HTTP headers string for ffmpeg
    headers = f"User-Agent: {_BASE_UA}\r\n"
    if item.referer:
        headers += f"Referer: {item.referer}\r\n"
    if item.cookies:
        headers += f"Cookie: {item.cookies}\r\n"

    cmd = [
        FFMPEG_BIN, "-y",
        "-headers", headers,
        "-i", item.url,
        "-c", "copy",           # stream copy — fast, no re-encode
        "-bsf:a", "aac_adtstoasc",  # fix audio for mp4 container
        "-movflags", "+faststart",
        "-progress", "pipe:1",  # machine-readable progress to stdout
        "-loglevel", "error",
        out_path,
    ]

    item.segments = 1  # ffmpeg is single-threaded per stream
    item.status = item.status  # keep current status

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # ffmpeg -progress outputs key=value pairs to stdout
    # e.g.:  out_time_us=4500000   →  4.5 seconds processed
    #        total_size=1048576     →  bytes written so far
    _FFMPEG_KV = re.compile(r"^(\w+)=(.+)$")
    kv: dict = {}

    async def _read_progress():
        assert proc.stdout
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            m = _FFMPEG_KV.match(line)
            if m:
                kv[m.group(1)] = m.group(2)
            if line == "progress=end" or "total_size" in kv:
                out_time_us = int(kv.get("out_time_us", 0) or 0)
                total_size  = int(kv.get("total_size", 0) or 0)
                speed_str   = kv.get("speed", "").replace("x", "")

                item.downloaded = total_size
                # HLS total size is unknown upfront — show bytes downloaded
                item.total      = 0
                item.progress   = min(99.0, item.progress + 0.5) if out_time_us else item.progress

                try:
                    spd = float(speed_str) if speed_str else 0.0
                    # speed here is realtime multiplier, not bytes/s — skip for now
                except ValueError:
                    pass

                if item.status == DownloadStatus.CANCELLED:
                    proc.kill()
                    return
                await notify(item)

    try:
        await asyncio.wait_for(_read_progress(), timeout=7200)  # 2h max
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

    item.progress   = 100.0
    item.downloaded = Path(out_path).stat().st_size if Path(out_path).exists() else item.downloaded


async def _media_via_aria2c(item, notify, loop, dest_dir):
    """
    Fast path:
      1. yt-dlp extract_info() → get real video URL + title + format
      2. aria2c downloads the actual bytes with 16 connections
    """

    def _extract():
        opts = {
            "quiet":              True,
            "no_warnings":        True,
            "cookiesfrombrowser": ("chrome",),
            # Pick best single-file format (no merge needed when possible)
            "format":             "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(item.url, download=False)

    info = await loop.run_in_executor(None, _extract)
    if not info:
        raise RuntimeError("yt-dlp could not extract info for this URL")

    # yt-dlp sometimes wraps single videos inside a playlist container
    entry = info
    if "entries" in info and info.get("entries"):
        entry = info["entries"][0]
        if entry is None:
            raise RuntimeError("yt-dlp returned an empty playlist entry")

    title = entry.get("title") or entry.get("id") or info.get("title") or "video"
    # Sanitise title for use as a filename
    title = re.sub(r'[\\/*?:"<>|]', "_", title).strip()
    item.filename = f"{title}.mp4"
    await notify(item)

    # Collect all URLs to download (could be video + audio separately)
    urls_to_download = []

    if "requested_formats" in entry:
        # yt-dlp selected separate video + audio tracks → download both, ffmpeg merge
        for fmt in entry["requested_formats"]:
            urls_to_download.append({
                "url":          fmt["url"],
                "ext":          fmt.get("ext", "mp4"),
                "protocol":     fmt.get("protocol", "https"),
                "http_headers": fmt.get("http_headers", {}),
            })
    elif entry.get("url"):
        # Single combined stream URL
        urls_to_download.append({
            "url":          entry["url"],
            "ext":          entry.get("ext", "mp4"),
            "protocol":     entry.get("protocol", "https"),
            "http_headers": entry.get("http_headers", {}),
        })
    elif entry.get("formats"):
        # No pre-selected URL — pick the best format ourselves
        formats = entry["formats"]
        best = None
        # Prefer a format that has both video and audio in one file
        for fmt in reversed(formats):
            if (fmt.get("url")
                    and fmt.get("vcodec", "none") != "none"
                    and fmt.get("acodec", "none") != "none"):
                best = fmt
                break
        if not best:
            # Fall back to last format that has any URL
            for fmt in reversed(formats):
                if fmt.get("url"):
                    best = fmt
                    break
        if not best:
            raise RuntimeError("yt-dlp found no downloadable format for this URL")
        urls_to_download.append({
            "url":          best["url"],
            "ext":          best.get("ext", "mp4"),
            "protocol":     best.get("protocol", "https"),
            "http_headers": best.get("http_headers", {}),
        })
    else:
        raise RuntimeError("yt-dlp could not find a download URL for this page")

    if len(urls_to_download) == 1:
        # Single file — aria2c downloads directly
        fmt = urls_to_download[0]
        video_url = fmt["url"]
        filename  = f"{title}.{fmt['ext']}"

        # Merge any site-specific headers with our session headers
        site_headers = fmt.get("http_headers", {})
        extra = []
        for k, v in site_headers.items():
            if k.lower() not in ("user-agent",):
                extra += [f"--header={k}: {v}"]

        item.filename = filename
        await _aria2c_download(item, notify, video_url, filename, str(dest_dir), extra)

    else:
        # Two tracks — download separately then merge with ffmpeg
        parts = []
        for i, fmt in enumerate(urls_to_download):
            part_name = f"{title}.part{i}.{fmt['ext']}"
            parts.append(str(dest_dir / part_name))

            site_headers = fmt.get("http_headers", {})
            extra = [f"--header={k}: {v}" for k, v in site_headers.items()
                     if k.lower() not in ("user-agent",)]

            temp_item = _shadow_item(item, part_name)
            await _aria2c_download(temp_item, notify, fmt["url"], part_name,
                                   str(dest_dir), extra)
            # Copy progress back
            item.downloaded = temp_item.downloaded
            item.total      = temp_item.total
            item.progress   = temp_item.progress

        # Merge with ffmpeg
        out_file = str(dest_dir / f"{title}.mp4")
        item.filename = f"{title}.mp4"
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg and len(parts) == 2:
            merge_cmd = [
                ffmpeg, "-y",
                "-i", parts[0],
                "-i", parts[1],
                "-c", "copy",
                out_file,
            ]
            proc = await asyncio.create_subprocess_exec(
                *merge_cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            for p in parts:
                Path(p).unlink(missing_ok=True)
        else:
            # No ffmpeg — rename first part as output
            Path(parts[0]).rename(out_file)
            for p in parts[1:]:
                Path(p).unlink(missing_ok=True)


async def _media_ytdlp_native(item, notify, loop, dest_dir):
    """
    Fallback: yt-dlp does URL extraction AND downloading.
    Slower but works without aria2c.
    """
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
    """Clone item with a different filename for parallel part downloads."""
    from dataclasses import replace
    shadow = replace(item, filename=new_filename, downloaded=0, total=0, progress=0.0)
    shadow._pause_event = item._pause_event   # share pause state
    return shadow


# ═══════════════════════════════════════════════════════════════════════════ #
#   aiohttp fallback (no aria2c)                                              #
# ═══════════════════════════════════════════════════════════════════════════ #

async def _aiohttp_download(item: DownloadItem, notify):
    """Single-stream aiohttp download — used only when aria2c is not installed."""
    dest = Path(item.save_path) / item.filename
    dest.parent.mkdir(parents=True, exist_ok=True)

    headers    = dict(_build_headers(item))
    resume_pos = 0

    if dest.exists():
        resume_pos = dest.stat().st_size
        if resume_pos > 0:
            headers["Range"] = f"bytes={resume_pos}-"

    timeout = aiohttp.ClientTimeout(connect=CONNECT_TIMEOUT, sock_read=READ_TIMEOUT)

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

            with open(dest, "ab" if resume_pos else "wb") as f:
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


# ── Media scanner ───────────────────────────────────────────────────────────

async def scan_media(url: str) -> list:
    if not YTDLP_AVAILABLE:
        return []
    loop = asyncio.get_event_loop()

    def _extract():
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                                "cookiesfrombrowser": ("chrome",)}) as ydl:
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


# ── Helpers ──────────────────────────────────────────────────────────────────

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
