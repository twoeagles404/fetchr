"""
Fetchr Base Scraper
All site scrapers inherit from this.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse


@dataclass
class ScrapedItem:
    url: str
    filename: Optional[str] = None
    referer: Optional[str] = None
    album: Optional[str] = None      # subfolder name
    dl_type: str = "direct"


class BaseScraper(ABC):
    DOMAINS: List[str] = []          # e.g. ["cyberdrop.me", "cyberdrop.cc"]

    @abstractmethod
    async def scrape(self, url: str, cookies: str = "") -> List[ScrapedItem]:
        """Extract all downloadable items from the given URL."""
        ...

    @classmethod
    def can_handle(cls, url: str) -> bool:
        try:
            host = urlparse(url).netloc.lower().replace("www.", "")
            return any(host == d or host.endswith("." + d) for d in cls.DOMAINS)
        except Exception:
            return False

    @staticmethod
    def _build_headers(cookies: str = "", referer: str = "") -> dict:
        h = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            )
        }
        if cookies:
            h["Cookie"] = cookies
        if referer:
            h["Referer"] = referer
        return h
