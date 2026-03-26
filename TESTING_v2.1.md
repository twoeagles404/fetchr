# Fetchr v2.1 — Testing Guide

## Prerequisites

You'll need these installed on the machine running the agent (your Proxmox VM or any Linux box):

```bash
# System deps
sudo apt install -y aria2 python3 python3-pip git

# Python deps
cd ~/fetchr/agent
pip install -r requirements.txt --break-system-packages
```

Confirm aria2c is available:
```bash
aria2c --version   # should show 1.36+
```

---

## 1. Start the Agent

```bash
cd ~/fetchr/agent
python main.py
```

You should see:
```
✅  Fetchr agent v2.0 listening on http://0.0.0.0:9876
   Web UI:      http://localhost:9876/
   Save folder: ~/Downloads/Fetchr
```

Open the web UI in your browser: **http://[your-server-ip]:9876/**

From your phone or another PC on the same network, use the server's LAN IP instead of localhost.

---

## 2. Basic Direct Download

This tests the full download pipeline: aria2c RPC → .part file → rename on completion.

1. In the URL bar, paste any direct file link, e.g.:
   ```
   https://speed.hetzner.de/100MB.bin
   ```
2. Click **Download**
3. Watch the progress bar update in real time via WebSocket
4. When done, check `~/Downloads/Fetchr/` — the file should be there (no `.part` leftover)

**What to verify:**
- Progress percentage and speed display correctly
- Status changes: `queued → active → complete`
- File exists on disk after completion

---

## 3. aria2c RPC Pause / Resume / Cancel

The old v2.0 "pause" was fake — aria2c kept downloading in the background. v2.1 uses real RPC pause.

1. Start a large download (the 100 MB test file works well)
2. While it's active, click **Pause** — speed should drop to 0 immediately
3. Click **Resume** — download continues from where it stopped (no restart)
4. Start another download, then click the **✕** cancel button — file and .part should be removed

**What to verify:**
- Speed truly stops on pause (check Task Manager / `htop` — aria2c process CPU drops)
- Resume picks up from the same byte offset (progress doesn't reset to 0)
- Cancelled download disappears from the list

---

## 4. Concurrent Downloads & Speed Limit

1. Queue 4–5 downloads at once
2. Only 3 should go `active` simultaneously (default `max_concurrent = 3`)
3. Go to **Settings → Max concurrent** and change it to 2 — the 3rd active download should pause-queue immediately
4. Set **Max speed (KB/s)** to `500` — active downloads should throttle to ~500 KB/s

---

## 5. Media Download (yt-dlp)

Tests YouTube, Twitter/X, Instagram Reels, etc.

1. Paste a YouTube URL: `https://www.youtube.com/watch?v=dQw4w9WgXcQ`
2. The smart URL bar will offer two buttons: **Download** and **Scan formats**
3. Click **Scan formats** first — you'll see a list of available resolutions/codecs
4. Close the scan, then click **Download** (it will use yt-dlp best quality)
5. File saves to `~/Downloads/Fetchr/`

---

## 6. Gallery Scraper (Bunkr / Cyberdrop)

Tests the link-grabber / batch download.

1. Paste a public Bunkr album URL, e.g.:
   ```
   https://bunkr.si/a/[any-public-album-id]
   ```
   Or a public Cyberdrop album:
   ```
   https://cyberdrop.me/a/[any-public-album-id]
   ```
2. The URL bar should switch to **Scrape** mode (blue button) automatically
3. Click **Scrape** — a modal appears listing all files in the album
4. Check/uncheck the ones you want, then click **Download Selected**
5. All selected files queue up simultaneously

**What to verify:**
- Scrape modal shows correct filenames and count
- All selected items appear in the queue
- Dedup: scrape the same album twice — second time all items should be skipped

---

## 7. Kemono / Coomer Scraper

1. Find a public Kemono creator page, e.g.:
   ```
   https://kemono.su/patreon/user/12345678
   ```
2. Paste the URL — Scrape button should appear
3. Click **Scrape** — all posts' attachments load (paginated, may take a few seconds for large creators)
4. Download a selection

---

## 8. URL Deduplication

1. Download any file to completion
2. Try to add the exact same URL again
3. You should get: `{"skipped": true, "reason": "URL already in download history"}`
4. The UI shows the item as skipped (not re-downloaded)

To reset dedup for testing:
- Go to **History** tab → **Clear History** button

---

## 9. Persistence / Crash Recovery

1. Start a large download (something that takes 30+ seconds)
2. While it's active, kill the agent: `Ctrl+C` in the terminal
3. Restart: `python main.py`
4. The download should re-appear in the queue and resume from 0 (aria2c doesn't persist its own queue, so it restarts the download — but the queue itself recovers)

**What to verify:**
- Queue is restored on startup (you'll see `↩  Restored 1 pending download(s) from last session`)

---

## 10. Push Notifications (ntfy — easiest to test)

ntfy is a free, no-account notification service. Perfect for homelab.

**Setup in 2 minutes:**
1. Install the ntfy app on your phone (iOS or Android) — it's free
2. Subscribe to a topic name you choose, e.g. `fetchr-ugo-2024`
3. In Fetchr **Settings → Notifications**, enter:
   ```
   ntfy://fetchr-ugo-2024
   ```
   Or use the public server explicitly:
   ```
   ntfy://ntfy.sh/fetchr-ugo-2024
   ```
4. Click **Save Settings**
5. Complete any download — you should get a push notification on your phone with the filename and file size

**Other services:**
- Telegram: `tgram://[bot-token]/[chat-id]`
- Discord: `discord://[webhook-id]/[webhook-token]`
- Gotify (self-hosted): `gotify://[host]/[app-token]`
- Email: `mailto://[user]:[pass]@gmail.com?to=[recipient]`

Full list: https://github.com/caronc/apprise/wiki

---

## 11. Auto-Extract

1. Find a direct link to a `.zip` file
2. In **Settings**, enable **Auto-extract archives**
3. Download the ZIP
4. After completion, the ZIP is extracted in-place. Original ZIP is kept.

---

## 12. Organise by Type

1. In **Settings**, enable **Organise by type**
2. Download a mix of files: a video, an image, a zip
3. Check `~/Downloads/Fetchr/`:
   - `.mp4` → `Videos/`
   - `.jpg` / `.png` → `Images/`
   - `.zip` → `Archives/`

---

## 13. LAN Access from Another Device

Since the agent listens on `0.0.0.0:9876`, any device on the same network can use it.

1. Find your server's LAN IP: `hostname -I | awk '{print $1}'`
2. On your phone or another PC, open: `http://[LAN-IP]:9876/`
3. Full UI should load — you can add and monitor downloads from there

---

## 14. API Smoke Test (optional, for the curious)

The FastAPI agent exposes a full REST API. You can poke it directly:

```bash
# Health check
curl http://localhost:9876/health

# Add a download
curl -X POST http://localhost:9876/downloads \
  -H "Content-Type: application/json" \
  -d '{"url":"https://speed.hetzner.de/10MB.bin"}'

# List all downloads
curl http://localhost:9876/downloads

# View history
curl http://localhost:9876/history

# Interactive API docs
open http://localhost:9876/docs
```

---

## Quick Troubleshooting

| Symptom | Fix |
|---|---|
| `aria2c: command not found` | `sudo apt install aria2` |
| Downloads stuck in `queued` | Check if aria2 RPC started: look for `aria2c --enable-rpc` in `ps aux` |
| Can't reach UI from LAN | Check your firewall: `sudo ufw allow 9876/tcp` |
| yt-dlp download fails | Update it: `pip install -U yt-dlp --break-system-packages` |
| Notifications not arriving | Test your Apprise URL at https://apprise.rocks first |
| Scraper returns 0 items | Site may require cookies — paste your browser cookies into the Cookies field |

---

## Docker (Proxmox LXC Recommended Setup)

If you want Fetchr running 24/7 as a container on Proxmox:

```bash
# On your Proxmox host, create an unprivileged LXC (Debian 12)
# Then inside the container:

apt update && apt install -y python3 python3-pip aria2 git
git clone https://github.com/twoeagles404/fetchr.git
cd fetchr/agent
pip install -r requirements.txt --break-system-packages

# Run on startup (add to /etc/rc.local or create a systemd unit):
nohup python3 main.py > /var/log/fetchr.log 2>&1 &
```

Systemd unit (recommended):
```ini
# /etc/systemd/system/fetchr.service
[Unit]
Description=Fetchr Download Agent
After=network.target

[Service]
WorkingDirectory=/root/fetchr/agent
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable fetchr
systemctl start fetchr
systemctl status fetchr
```

Access from any device on your network: `http://[proxmox-lxc-ip]:9876/`
