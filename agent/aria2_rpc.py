"""
Fetchr aria2c RPC Daemon
──────────────────────────────────────────────────────────────────────────────
Replaces the one-subprocess-per-download approach with a single persistent
aria2c daemon controlled via its JSON-RPC API.

Why this is better than spawning aria2c per download:
  • Real pause/resume — calls aria2.pause(gid) / aria2.unpause(gid)
  • Real cancellation — calls aria2.remove(gid), no zombie processes
  • No stdout parsing — progress comes from structured JSON API
  • Lower overhead — one process, many downloads

Usage:
  from aria2_rpc import aria2, ARIA2_RPC_AVAILABLE

  # On agent startup:
  ok = await aria2.start_daemon()

  # Add a download:
  gid = await aria2.add_uri(url, filename, dest_dir, referer=..., cookies=...)

  # Poll for progress:
  status = await aria2.tell_status(gid)

  # Control:
  await aria2.pause(gid)
  await aria2.unpause(gid)
  await aria2.remove(gid)

  # On shutdown:
  await aria2.stop_daemon()
──────────────────────────────────────────────────────────────────────────────
"""

import asyncio
import shutil
import uuid
from typing import Optional

import aiohttp

# ── Config ────────────────────────────────────────────────────────────────────

ARIA2C_RPC_PORT    = 6800
ARIA2C_RPC_SECRET  = "fetchr_rpc_2024"
ARIA2C_CONNECTIONS = 16


class Aria2RPC:
    """
    Manages one aria2c daemon process and exposes its JSON-RPC API
    as clean async methods.
    """

    def __init__(self):
        self.port    = ARIA2C_RPC_PORT
        self.secret  = ARIA2C_RPC_SECRET
        self.rpc_url = f"http://localhost:{self.port}/jsonrpc"
        self._proc:    Optional[asyncio.subprocess.Process] = None
        self._session: Optional[aiohttp.ClientSession]      = None
        self.available = False

    # ── Daemon lifecycle ──────────────────────────────────────────────────────

    async def start_daemon(self) -> bool:
        """
        Launch aria2c in RPC mode.  Returns True if the daemon started and
        the RPC endpoint is responding.
        """
        bin_ = shutil.which("aria2c")
        if not bin_:
            print("⚠️   aria2c not found — RPC mode unavailable, falling back to subprocess")
            return False

        cmd = [
            bin_,
            "--enable-rpc=true",
            f"--rpc-listen-port={self.port}",
            f"--rpc-secret={self.secret}",
            "--rpc-allow-origin-all=true",
            "--rpc-listen-all=false",          # localhost only
            "--quiet=true",
            "--console-log-level=warn",
            # Download tuning
            f"--max-connection-per-server={ARIA2C_CONNECTIONS}",
            f"--split={ARIA2C_CONNECTIONS}",
            "--min-split-size=1M",
            "--file-allocation=none",
            "--auto-file-renaming=false",
            "--allow-overwrite=true",
            "--retry-wait=3",
            "--max-tries=8",
            "--connect-timeout=15",
            "--timeout=60",
            # Keep running as a daemon we control
            "--daemon=false",
        ]

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        # Poll until RPC responds (up to 4 seconds)
        for _ in range(20):
            await asyncio.sleep(0.2)
            try:
                await self.get_global_stat()
                self.available = True
                print(f"✅  aria2c RPC daemon started on port {self.port}")
                return True
            except Exception:
                pass

        print("⚠️   aria2c RPC daemon did not respond — falling back to subprocess mode")
        return False

    async def stop_daemon(self) -> None:
        """Gracefully shut down the aria2c daemon."""
        try:
            await self._call("aria2.shutdown")
        except Exception:
            pass

        if self._session:
            await self._session.close()
            self._session = None

        if self._proc:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except Exception:
                self._proc.kill()
            self._proc = None

        self.available = False
        print("🛑  aria2c RPC daemon stopped")

    # ── RPC transport ─────────────────────────────────────────────────────────

    async def _call(self, method: str, params: list | None = None) -> object:
        """Send a JSON-RPC call and return the result."""
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()

        payload = {
            "jsonrpc": "2.0",
            "id":      str(uuid.uuid4()),
            "method":  method,
            "params":  [f"token:{self.secret}"] + (params or []),
        }

        async with self._session.post(
            self.rpc_url,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            data = await resp.json(content_type=None)
            if "error" in data:
                raise RuntimeError(
                    f"aria2c RPC [{method}]: {data['error'].get('message', data['error'])}"
                )
            return data.get("result")

    # ── Download control ──────────────────────────────────────────────────────

    async def add_uri(
        self,
        url:           str,
        filename:      str,
        dest_dir:      str,
        referer:       str = "",
        cookies:       str = "",
        extra_headers: dict | None = None,
        user_agent:    str = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    ) -> str:
        """
        Queue a URI for download.  Returns the GID (global ID) string
        that identifies this download for all subsequent calls.
        """
        options: dict = {
            "dir":                          dest_dir,
            "out":                          filename + ".part",
            "max-connection-per-server":    str(ARIA2C_CONNECTIONS),
            "split":                        str(ARIA2C_CONNECTIONS),
            "user-agent":                   user_agent,
        }

        headers: list[str] = []
        if referer:
            options["referer"] = referer
        if cookies:
            headers.append(f"Cookie: {cookies}")
        if extra_headers:
            for k, v in extra_headers.items():
                if k.lower() != "user-agent":
                    headers.append(f"{k}: {v}")
        if headers:
            options["header"] = headers

        return await self._call("aria2.addUri", [[url], options])

    async def tell_status(self, gid: str) -> dict:
        """
        Return a status dict for the given GID.

        Key fields:
          status          — "active" | "waiting" | "paused" | "error" | "complete" | "removed"
          completedLength — bytes downloaded so far (string)
          totalLength     — total file size in bytes (string, 0 if unknown)
          downloadSpeed   — current speed in bytes/s (string)
          errorCode       — aria2c error code if status == "error"
          errorMessage    — human-readable error description
        """
        return await self._call("aria2.tellStatus", [gid, [
            "status",
            "completedLength",
            "totalLength",
            "downloadSpeed",
            "errorCode",
            "errorMessage",
            "files",
        ]])

    async def pause(self, gid: str) -> None:
        """Pause an active download.  aria2c stops writing to disk."""
        try:
            await self._call("aria2.pause", [gid])
        except Exception as e:
            print(f"⚠️   aria2c pause failed ({gid}): {e}")

    async def unpause(self, gid: str) -> None:
        """Resume a paused download."""
        try:
            await self._call("aria2.unpause", [gid])
        except Exception as e:
            print(f"⚠️   aria2c unpause failed ({gid}): {e}")

    async def remove(self, gid: str) -> None:
        """Cancel and remove a download (active or queued)."""
        try:
            await self._call("aria2.remove", [gid])
        except Exception:
            pass
        try:
            await self._call("aria2.removeDownloadResult", [gid])
        except Exception:
            pass

    async def get_global_stat(self) -> dict:
        """
        Return global download stats:
          downloadSpeed, uploadSpeed, numActive, numWaiting, numStopped
        """
        return await self._call("aria2.getGlobalStat")


# ── Singleton ─────────────────────────────────────────────────────────────────
aria2 = Aria2RPC()
ARIA2_RPC_AVAILABLE = shutil.which("aria2c") is not None
