"""
Fetchr Erome Scraper
Handles erome.com album pages.
Extracts images and videos from album pages using HTML parsing.
"""
import aiohttp
import re
from typing import List, Optional
from bs4 import BeautifulSoup
from .base import BaseScraper, ScrapedItem


class EromeScraper(BaseScraper):
    DOMAINS = ["erome.com", "www.erome.com"]

    async def scrape(self, url: str, cookies: str = "") -> List[ScrapedItem]:
        """
        Scrape Erome album URLs.
        Handles:
        - https://www.erome.com/a/{album_id}
        """
        try:
            headers = self._build_headers(cookies=cookies, referer=url)
            items = []

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        print(f"Erome: Failed to fetch {url}")
                        return []
                    html = await resp.text()

            soup = BeautifulSoup(html, "html.parser")
            items = self._extract_items(soup, url)

            print(f"Erome: Found {len(items)} items from {url}")
            return items

        except Exception as e:
            print(f"Erome scraper error: {e}")
            return []

    def _extract_items(self, soup: BeautifulSoup, page_url: str) -> List[ScrapedItem]:
        """Extract images and videos from Erome album page."""
        items = []

        # Get album title
        album_title = None
        h1 = soup.find("h1", class_="album-profile-name")
        if h1:
            album_title = h1.get_text(strip=True)
        
        if not album_title:
            title_tag = soup.find("title")
            if title_tag:
                album_title = title_tag.get_text(strip=True)
        
        if not album_title:
            album_title = "erome_album"

        # Find album container
        album_container = soup.find("div", class_="item-page-album")
        if not album_container:
            return items

        # Extract images from img tags
        for img in album_container.find_all("img", class_="img-back"):
            src = img.get("src") or img.get("data-src")
            if src and "erome.com" in src:
                # Skip thumbnails
                if "thumb" not in src:
                    item = ScrapedItem(
                        url=src,
                        album=album_title,
                        referer=page_url,
                        dl_type="direct"
                    )
                    items.append(item)

        # Extract videos from source tags
        for source in album_container.find_all("source"):
            src = source.get("src")
            if src and ("erome.com" in src or src.endswith(".mp4")):
                item = ScrapedItem(
                    url=src,
                    album=album_title,
                    referer=page_url,
                    dl_type="direct"
                )
                items.append(item)

        # Also check for video tags with src attribute
        for video in album_container.find_all("video"):
            src = video.get("src")
            if src and ("erome.com" in src or src.endswith(".mp4")):
                item = ScrapedItem(
                    url=src,
                    album=album_title,
                    referer=page_url,
                    dl_type="direct"
                )
                items.append(item)

        return items
