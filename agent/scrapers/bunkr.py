"""
Fetchr Bunkr Scraper
Handles multiple Bunkr domains for album pages
"""
import aiohttp
from typing import List
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from .base import BaseScraper, ScrapedItem


class BunkrScraper(BaseScraper):
    DOMAINS = [
        "bunkr.site",
        "bunkr.black",
        "bunkr.cr",
        "bunkr.fi",
        "bunkr.ph",
        "bunkr.sk"
    ]

    async def scrape(self, url: str, cookies: str = "") -> List[ScrapedItem]:
        """
        Scrape Bunkr album pages.
        Extracts download links from file pages and CDN.
        """
        try:
            headers = self._build_headers(cookies=cookies, referer=url)
            items = []

            async with aiohttp.ClientSession() as session:
                # Fetch the album page
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        print(f"Bunkr: Failed to fetch {url} (status {resp.status})")
                        return []

                    html = await resp.text()

            soup = BeautifulSoup(html, "html.parser")

            # Extract album title
            album_title = None
            h1 = soup.find("h1")
            if h1:
                album_title = h1.get_text(strip=True)

            # Strategy 1: Look for grid-images container with file links
            grid = soup.find("div", {"class": "grid-images"})
            if grid:
                # Find all links to individual file pages
                file_links = []
                for link in grid.find_all("a", href=True):
                    href = link.get("href", "")
                    if href:
                        # Make absolute URL
                        abs_url = urljoin(url, href)
                        file_links.append(abs_url)

                # Fetch each file page and extract the direct download link
                if file_links:
                    async with aiohttp.ClientSession() as session:
                        for file_url in file_links:
                            try:
                                async with session.get(
                                    file_url,
                                    headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=30)
                                ) as resp:
                                    if resp.status == 200:
                                        file_html = await resp.text()
                                        file_soup = BeautifulSoup(file_html, "html.parser")

                                        # Look for <source> tag (video) or <img> (image)
                                        download_url = None
                                        filename = None

                                        # Try to get filename from page title or heading
                                        title_tag = file_soup.find("title")
                                        if title_tag:
                                            filename = title_tag.get_text(strip=True)
                                            # Remove domain from title if present
                                            if " - " in filename:
                                                filename = filename.split(" - ")[0].strip()

                                        # Look for source tag (video files)
                                        source = file_soup.find("source", {"src": True})
                                        if source:
                                            download_url = source.get("src", "")

                                        # Look for <img> tag or direct CDN link
                                        if not download_url:
                                            img = file_soup.find("img")
                                            if img:
                                                download_url = img.get("src", "")

                                        # Look for download button / link pointing to CDN
                                        if not download_url:
                                            for a in file_soup.find_all("a", href=True):
                                                href = a.get("href", "")
                                                if "cdn" in href.lower() or "bunkr" in href.lower() or href.startswith("http"):
                                                    download_url = href
                                                    break

                                        if download_url:
                                            items.append(ScrapedItem(
                                                url=download_url,
                                                filename=filename,
                                                referer=file_url,
                                                album=album_title,
                                                dl_type="direct"
                                            ))
                            except Exception as e:
                                print(f"Bunkr: Error fetching file page {file_url}: {e}")
                                continue

            # Strategy 2: Direct CDN links in the page (fallback)
            if not items:
                for a in soup.find_all("a", href=True):
                    href = a.get("href", "")
                    if "cdn" in href.lower() or ("media" in href.lower() and "bunkr" in href.lower()):
                        items.append(ScrapedItem(
                            url=href,
                            filename=a.get_text(strip=True) or None,
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

            print(f"Bunkr: Found {len(unique_items)} items from {url}")
            return unique_items

        except Exception as e:
            print(f"Bunkr scraper error: {e}")
            return []
