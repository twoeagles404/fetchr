"""
Fetchr Imgur Scraper
Handles albums, galleries, and single images
"""
import aiohttp
import re
import json
from typing import List, Optional
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from .base import BaseScraper, ScrapedItem


class ImgurScraper(BaseScraper):
    DOMAINS = ["imgur.com"]

    async def scrape(self, url: str, cookies: str = "") -> List[ScrapedItem]:
        """
        Scrape Imgur URLs.
        Handles:
        - /a/{album_id} - albums
        - /gallery/{gallery_id} - galleries
        - /{image_id} - single images
        """
        try:
            headers = self._build_headers(cookies=cookies, referer=url)
            items = []

            # Determine URL type
            if "/a/" in url:
                items = await self._scrape_album(url, headers)
            elif "/gallery/" in url:
                items = await self._scrape_gallery(url, headers)
            else:
                # Single image
                items = await self._scrape_single_image(url, headers)

            print(f"Imgur: Found {len(items)} items from {url}")
            return items

        except Exception as e:
            print(f"Imgur scraper error: {e}")
            return []

    async def _scrape_album(
        self,
        url: str,
        headers: dict
    ) -> List[ScrapedItem]:
        """Scrape an Imgur album."""
        try:
            async with aiohttp.ClientSession() as session:
                # Try the blog layout for better HTML
                album_url = url.rstrip("/")
                if not album_url.endswith("/layout/blog"):
                    album_url += "/layout/blog"

                async with session.get(album_url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        # Fallback to regular URL
                        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp2:
                            if resp2.status != 200:
                                print(f"Imgur: Failed to fetch album {url}")
                                return []
                            html = await resp2.text()
                    else:
                        html = await resp.text()

            soup = BeautifulSoup(html, "html.parser")
            items = []

            # Extract album title
            album_title = None
            h1 = soup.find("h1")
            if h1:
                album_title = h1.get_text(strip=True)

            # Strategy 1: Look for image URLs in <img> tags
            for img in soup.find_all("img"):
                src = img.get("src", "")
                alt = img.get("alt", "")

                if src and ("imgur.com" in src or ".jpg" in src or ".png" in src or ".gif" in src):
                    # Extract image ID and construct direct URL
                    media_url = self._construct_imgur_url(src)
                    if media_url:
                        items.append(ScrapedItem(
                            url=media_url,
                            filename=alt or None,
                            referer=url,
                            album=album_title,
                            dl_type="direct"
                        ))

            # Strategy 2: Look for video sources
            for source in soup.find_all("source"):
                src = source.get("src", "")
                if src and (".mp4" in src or ".webm" in src):
                    items.append(ScrapedItem(
                        url=src,
                        filename=None,
                        referer=url,
                        album=album_title,
                        dl_type="direct"
                    ))

            # Strategy 3: Look for data in script tags (Imgur embeds JSON)
            for script in soup.find_all("script"):
                script_text = script.string
                if script_text and ("window.postDataJSON" in script_text or "image:" in script_text):
                    # Try to extract URLs from JSON-like content
                    urls = re.findall(r'"url":"([^"]+)"', script_text)
                    for url_match in urls:
                        if "imgur" in url_match or url_match.endswith((".jpg", ".png", ".gif", ".mp4")):
                            items.append(ScrapedItem(
                                url=url_match,
                                filename=None,
                                referer=url,
                                album=album_title,
                                dl_type="direct"
                            ))

            # Deduplicate
            seen = set()
            unique_items = []
            for item in items:
                if item.url not in seen:
                    seen.add(item.url)
                    unique_items.append(item)

            return unique_items

        except Exception as e:
            print(f"Imgur: Error scraping album: {e}")
            return []

    async def _scrape_gallery(
        self,
        url: str,
        headers: dict
    ) -> List[ScrapedItem]:
        """Scrape an Imgur gallery (same as album)."""
        return await self._scrape_album(url, headers)

    async def _scrape_single_image(
        self,
        url: str,
        headers: dict
    ) -> List[ScrapedItem]:
        """Scrape a single Imgur image."""
        try:
            # Extract image ID from URL
            # Format: https://imgur.com/{id} or https://imgur.com/{id}.{ext}
            parts = url.rstrip("/").split("/")
            image_id = parts[-1]

            # Remove extension if present
            if "." in image_id:
                image_id = image_id.split(".")[0]

            # Try common formats
            urls_to_try = [
                f"https://i.imgur.com/{image_id}.jpg",
                f"https://i.imgur.com/{image_id}.png",
                f"https://i.imgur.com/{image_id}.gif",
                f"https://i.imgur.com/{image_id}.mp4",
            ]

            async with aiohttp.ClientSession() as session:
                for try_url in urls_to_try:
                    try:
                        async with session.head(try_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True) as resp:
                            if resp.status == 200:
                                return [ScrapedItem(
                                    url=try_url,
                                    filename=f"{image_id}{try_url[-4:]}",
                                    referer=url,
                                    album=None,
                                    dl_type="direct"
                                )]
                    except Exception:
                        continue

            # Fallback: fetch the page and look for media
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        soup = BeautifulSoup(html, "html.parser")

                        # Look for img or video tags
                        for img in soup.find_all("img"):
                            src = img.get("src", "")
                            if src and "imgur" in src:
                                media_url = self._construct_imgur_url(src)
                                if media_url:
                                    return [ScrapedItem(
                                        url=media_url,
                                        filename=f"{image_id}",
                                        referer=url,
                                        album=None,
                                        dl_type="direct"
                                    )]

                        for video in soup.find_all("video"):
                            source = video.find("source")
                            if source:
                                src = source.get("src", "")
                                if src:
                                    return [ScrapedItem(
                                        url=src,
                                        filename=f"{image_id}.mp4",
                                        referer=url,
                                        album=None,
                                        dl_type="direct"
                                    )]

            return []

        except Exception as e:
            print(f"Imgur: Error scraping single image: {e}")
            return []

    @staticmethod
    def _construct_imgur_url(url: str) -> Optional[str]:
        """
        Construct a direct Imgur URL from various formats.
        Handles .gifv conversion to .mp4
        """
        try:
            # If already a direct CDN URL, return as-is
            if url.startswith("https://i.imgur.com/"):
                if url.endswith(".gifv"):
                    return url[:-5] + ".mp4"
                return url

            # Extract image ID
            match = re.search(r"(\w+)\.(jpg|png|gif|gifv|mp4|webm)", url)
            if match:
                image_id = match.group(1)
                ext = match.group(2)
                if ext == "gifv":
                    ext = "mp4"
                return f"https://i.imgur.com/{image_id}.{ext}"

            return None

        except Exception:
            return None
