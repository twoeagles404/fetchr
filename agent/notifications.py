"""
Fetchr Notification System — Apprise integration
──────────────────────────────────────────────────────────────────────────────
Supports 70+ services through a single URL string:

  ntfy (self-hosted, free):
    ntfy://your-server.com/fetchr
    ntfys://ntfy.sh/your-topic          ← public ntfy.sh

  Gotify (Proxmox homelab favourite):
    gotify://your-server.com/AppToken

  Telegram:
    tgram://BotToken/ChatID

  Discord webhook:
    discord://WebhookID/WebhookToken

  Slack:
    slack://TokenA/TokenB/TokenC/Channel

  Email (SMTP):
    mailtos://user:password@smtp.host/to@email.com

Configure in Settings → Notification URL, or directly in settings.json:
  "notification_url": "ntfy://192.168.1.50/fetchr"
──────────────────────────────────────────────────────────────────────────────
"""

import asyncio
from typing import Optional

try:
    import apprise
    APPRISE_AVAILABLE = True
except ImportError:
    APPRISE_AVAILABLE = False
    print("ℹ️   apprise not installed — notifications disabled")
    print("     Install: pip install apprise")


_ap: Optional[object] = None
_url: str = ""


def configure(notification_url: str) -> bool:
    """
    Set (or update) the notification target URL.
    Returns True if Apprise loaded the URL successfully.
    """
    global _ap, _url

    if not APPRISE_AVAILABLE:
        return False

    if not notification_url or not notification_url.strip():
        _ap  = None
        _url = ""
        return False

    notification_url = notification_url.strip()
    new_ap = apprise.Apprise()
    ok = new_ap.add(notification_url)

    if ok:
        _ap  = new_ap
        _url = notification_url
        print(f"🔔  Notifications configured: {notification_url}")
    else:
        print(f"⚠️   Invalid notification URL: {notification_url}")

    return ok


def is_configured() -> bool:
    return APPRISE_AVAILABLE and _ap is not None


# ── Notification senders ──────────────────────────────────────────────────────

async def notify_complete(filename: str, file_size: int = 0) -> None:
    """Fire a 'download complete' notification."""
    if not is_configured():
        return

    size_str = _fmt_size(file_size) if file_size else ""
    body = filename
    if size_str:
        body += f"\n{size_str}"

    await _send(
        title="✅ Fetchr — Download Complete",
        body=body,
    )


async def notify_error(filename: str, error: str = "") -> None:
    """Fire a 'download failed' notification."""
    if not is_configured():
        return

    body = filename
    if error:
        body += f"\n{error[:200]}"

    await _send(
        title="❌ Fetchr — Download Failed",
        body=body,
    )


async def notify_batch_complete(count: int, total_size: int = 0) -> None:
    """Fire a notification when a batch/gallery download finishes."""
    if not is_configured():
        return

    size_str = _fmt_size(total_size) if total_size else ""
    body = f"{count} file{'s' if count != 1 else ''} downloaded"
    if size_str:
        body += f" · {size_str} total"

    await _send(
        title="✅ Fetchr — Batch Complete",
        body=body,
    )


# ── Internals ─────────────────────────────────────────────────────────────────

async def _send(title: str, body: str) -> None:
    """Send via Apprise in a thread executor (Apprise is sync)."""
    if not is_configured():
        return
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None,
            lambda: _ap.notify(title=title, body=body),
        )
    except Exception as e:
        print(f"⚠️   Notification failed: {e}")


def _fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"
