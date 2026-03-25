"""
Fetchr Per-Domain Rate Limiter
Inspired by CyberDropDownloader's AsyncLimiter approach.

One token-bucket per domain — prevents hammering a single host
when batch-downloading dozens of files from it simultaneously.

Default: 25 requests/second per domain (matches CDL's default).
Scrapers and the downloader call `domain_limiter.acquire(url)`
before any outbound HTTP request.
"""

import asyncio
import time
from urllib.parse import urlparse
from typing import Dict


class DomainRateLimiter:
    """
    Async token-bucket rate limiter, one bucket per domain.
    Safe for concurrent use — each bucket has its own async lock.
    """

    def __init__(self, default_rps: float = 25.0):
        self.default_rps = default_rps
        self._buckets: Dict[str, "_TokenBucket"] = {}
        self._registry_lock = asyncio.Lock()

    # ── Public ────────────────────────────────────────────────────────────────

    async def acquire(self, url: str, rps: float | None = None) -> None:
        """
        Wait until a request token is available for this URL's domain.
        Call this before every outbound HTTP request.
        """
        domain = self._domain(url)
        rate   = rps or self.default_rps
        bucket = await self._get_bucket(domain, rate)
        await bucket.acquire()

    def set_domain_rate(self, domain: str, rps: float) -> None:
        """Override the rate limit for a specific domain (e.g. slow-hosts)."""
        self._buckets[domain] = _TokenBucket(rps)

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _domain(url: str) -> str:
        try:
            host = urlparse(url).netloc.lower()
            return host.split(":")[0]   # strip port number
        except Exception:
            return "unknown"

    async def _get_bucket(self, domain: str, rate: float) -> "_TokenBucket":
        async with self._registry_lock:
            if domain not in self._buckets:
                self._buckets[domain] = _TokenBucket(rate)
            return self._buckets[domain]


class _TokenBucket:
    """
    Single-domain token bucket.
    Tokens refill at `rate` per second, capped at `rate` (one second's worth).
    """

    def __init__(self, rate: float):
        self.rate   = rate
        self.tokens = rate          # start full — first request is instant
        self._last  = time.monotonic()
        self._lock  = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            # Refill based on elapsed time
            now            = time.monotonic()
            elapsed        = now - self._last
            self.tokens    = min(self.rate, self.tokens + elapsed * self.rate)
            self._last     = now

            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return

            wait = (1.0 - self.tokens) / self.rate

        # Sleep outside the lock so other coroutines aren't blocked
        await asyncio.sleep(wait)
        await self.acquire()          # re-acquire after sleeping


# ── Singleton — import and call directly ─────────────────────────────────────
domain_limiter = DomainRateLimiter(default_rps=25.0)
