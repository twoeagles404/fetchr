"""
Fetchr Saint2.su Scraper
Handles saint2.su and saint.to video hosting pages.
Extracts direct video URLs from page source and JavaScript.
"""
import aiohttp
import re
from typing import List, Optional
from bs4 import BeautifulSoup
from .base import BaseScraper, ScrapedItem


class Saint2Scraper(BaseScraper):
    DOMAINS = ["saint2.su", "saint.to"]

    async def scrape(self, url: str, cookies: str = "") -> List[ScrapedItem]:
        """
        Scrape Saint2 video hosting URLs.
        Handles:
        - https://saint2.su/embed/{id}
        - https://saint2.su/{id}
        - https://saint.to/embed/{id}
        - https://saint.to/{id}
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
                        print(f"Saint2: Failed to fetch {url}")
                        return []
                    html = await resp.text()

            items = self._extract_video_url(html, url)

            print(f"Saint2: Found {len(items)} items from {url}")
            return items

        except Exception as e:
            print(f"Saint2 scraper error: {e}")
            return []

    def _extract_video_url(self, html: str, page_url: str) -> List[ScrapedItem]:
        """Extract video URL from page HTML and JavaScript."""
        items = []

        soup = BeautifulSoup(html, "html.parser")

        # Strategy 1: Look for <source> tag in <video> element
        video_tag = soup.find("video")
        if video_tag:
            source_tag = video_tag.find("source", type="video/mp4")
            if source_tag:
                src = source_tag.get("src")
                if src:
                    item = self._create_item(src, page_url)
                    if item:
                        items.append(item)

        # Strategy 2: Look for src attribute directly on video tag
        if not items:
            video_tag = soup.find("video")
            if video_tag:
                src = video_tag.get("src")
                if src and src.endswith(".mp4"):
                    item = self._create_item(src, page_url)
                    if item:
                        items.append(item)

        # Strategy 3: Search in JavaScript for video URL patterns
        if not items:
            # Look for var source = "..."
            source_match = re.search(r'var\s+source\s*=\s*["\']([^"\']+\.mp4)["\']', html)
            if source_match:
                src = source_match.group(1)
                item = self._create_item(src, page_url)
                if item:
                    items.append(item)

        # Strategy 4: Look for file: "..." pattern in JavaScript
        if not items:
            file_match = re.search(r'file\s*:\s*["\']([^"\']+\.mp4)["\']', html)
            if file_match:
                src = file_match.group(1)
                item = self._create_item(src, page_url)
                if item:
                    items.append(item)

        # Strategy 5: Look for any mp4 URLs in script tags
        if not items:
            scripts = soup.find_all("script")
            for script in scripts:
                if script.string:
                    mp4_match = re.search(r'(["\'])([^"\']*\.mp4)\1', script.string)
                    if mp4_match:
                        src = mp4_match.group(2)
                        if src.startswith("http"):
                            item = self._create_item(src, page_url)
                            if item:
                                items.append(item)
                                break

        return items

    def _create_item(
        self,
        url: str,
        page_url: str
    ) -> Optional[ScrapedItem]:
        """Create a ScrapedItem from a video URL."""
        try:
            if not url:
                return None

            # Ensure URL is absolute
            if not url.startswith("http"):
                # Parse page URL to get domain
                from urllib.parse import urljoin
                url = urljoin(page_url, url)

            # Extract filename from URL
            filename = self._extract_filename(url)

            return ScrapedItem(
                url=url,
                filename=filename,
                referer=page_url,
                dl_type="direct"
            )
        except Exception as e:
            print(f"Saint2: Error creating item: {e}")
            return None

    @staticmethod
    def _extract_filename(url: str) -> str:
        """Extract filename from URL."""
        # Remove query parameters
        url_path = url.split("?")[0]
        # Get last part of path
        filename = url_path.split("/")[-1]
        return filename if filename else "video.mp4"
