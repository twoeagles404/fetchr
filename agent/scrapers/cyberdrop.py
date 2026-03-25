"""
Fetchr Cyberdrop Scraper
Handles https://cyberdrop.me, https://cyberdrop.cc album pages
"""
import aiohttp
from typing import List
from bs4 import BeautifulSoup
from .base import BaseScraper, ScrapedItem


class CyberdropScraper(BaseScraper):
    DOMAINS = ["cyberdrop.me", "cyberdrop.cc"]

    async def scrape(self, url: str, cookies: str = "") -> List[ScrapedItem]:
        """
        Scrape Cyberdrop album pages.
        Extracts direct download links from CDN (fs-01.cyberdrop.me, etc.)
        """
        try:
            headers = self._build_headers(cookies=cookies, referer=url)

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        print(f"Cyberdrop: Failed to fetch {url} (status {resp.status})")
                        return []

                    html = await resp.text()

            soup = BeautifulSoup(html, "html.parser")
            items = []

            # Extract album title from <h1 id="title"> or similar
            album_title = None
            h1 = soup.find("h1", {"id": "title"})
            if h1:
                album_title = h1.get_text(strip=True)

            # Look for image containers and download links
            # Cyberdrop typically has <a class="image"> or <div class="image-container"> with links

            # Strategy 1: Look for <a> tags with href containing /f/ (file links)
            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                # Look for direct CDN links or file page links
                if "/f/" in href or href.startswith("http"):
                    # Check if it's a direct CDN link
                    if "cyberdrop" in href or "fs-" in href:
                        filename = link.get_text(strip=True) or None
                        items.append(ScrapedItem(
                            url=href,
                            filename=filename,
                            referer=url,
                            album=album_title,
                            dl_type="direct"
                        ))

            # Strategy 2: Look for <img> tags inside image-container divs
            for container in soup.find_all("div", {"class": "image-container"}):
                img = container.find("img")
                if img:
                    src = img.get("src", "")
                    if src and ("cyberdrop" in src or "fs-" in src):
                        # Try to find a download link near this image
                        parent_link = container.find("a", href=True)
                        if parent_link:
                            dl_href = parent_link.get("href", "")
                            if dl_href:
                                items.append(ScrapedItem(
                                    url=dl_href,
                                    filename=None,
                                    referer=url,
                                    album=album_title,
                                    dl_type="direct"
                                ))

            # Strategy 3: Parse <p class="name"> for filenames
            for name_tag in soup.find_all("p", {"class": "name"}):
                filename = name_tag.get_text(strip=True)
                # Try to find associated download link
                parent = name_tag.find_parent("a", href=True)
                if parent:
                    href = parent.get("href", "")
                    if href and ("cyberdrop" in href or "fs-" in href or "/f/" in href):
                        items.append(ScrapedItem(
                            url=href,
                            filename=filename,
                            referer=url,
                            album=album_title,
                            dl_type="direct"
                        ))

            # Deduplicate by URL
            seen = set()
            unique_items = []
            for item in items:
                if item.url not in seen:
                    seen.add(item.url)
                    unique_items.append(item)

            print(f"Cyberdrop: Found {len(unique_items)} items from {url}")
            return unique_items

        except Exception as e:
            print(f"Cyberdrop scraper error: {e}")
            return []
