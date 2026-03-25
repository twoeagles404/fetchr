"""
Fetchr PixelDrain Scraper
Handles single files and file lists
"""
import aiohttp
from typing import List
from .base import BaseScraper, ScrapedItem


class PixelDrainScraper(BaseScraper):
    DOMAINS = ["pixeldrain.com"]

    async def scrape(self, url: str, cookies: str = "") -> List[ScrapedItem]:
        """
        Scrape PixelDrain URLs.
        Handles:
        - /u/{file_id} - single file
        - /l/{list_id} - list of files
        """
        try:
            headers = self._build_headers(cookies=cookies, referer=url)
            items = []

            # Determine if this is a file or list
            # Format: https://pixeldrain.com/u/{file_id} or /l/{list_id}
            parts = url.rstrip("/").split("/")
            if len(parts) < 2:
                print(f"PixelDrain: Invalid URL format: {url}")
                return []

            url_type = parts[-2]  # "u" or "l"
            content_id = parts[-1]

            async with aiohttp.ClientSession() as session:
                if url_type == "u":
                    # Single file
                    items = await self._scrape_file(session, content_id, headers)

                elif url_type == "l":
                    # List of files
                    items = await self._scrape_list(session, content_id, headers)

                else:
                    print(f"PixelDrain: Unknown URL type: {url_type}")
                    return []

            print(f"PixelDrain: Found {len(items)} items from {url}")
            return items

        except Exception as e:
            print(f"PixelDrain scraper error: {e}")
            return []

    async def _scrape_file(
        self,
        session: aiohttp.ClientSession,
        file_id: str,
        headers: dict
    ) -> List[ScrapedItem]:
        """Scrape a single PixelDrain file."""
        try:
            # Get file info
            info_url = f"https://pixeldrain.com/api/file/{file_id}/info"
            async with session.get(info_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    print(f"PixelDrain: Failed to get file info for {file_id}")
                    return []

                info = await resp.json()

            filename = info.get("name", file_id)
            download_url = f"https://pixeldrain.com/api/file/{file_id}"

            return [ScrapedItem(
                url=download_url,
                filename=filename,
                referer=None,
                album=None,
                dl_type="direct"
            )]

        except Exception as e:
            print(f"PixelDrain: Error scraping file {file_id}: {e}")
            return []

    async def _scrape_list(
        self,
        session: aiohttp.ClientSession,
        list_id: str,
        headers: dict
    ) -> List[ScrapedItem]:
        """Scrape a PixelDrain list of files."""
        try:
            # Get list contents
            list_url = f"https://pixeldrain.com/api/list/{list_id}"
            async with session.get(list_url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    print(f"PixelDrain: Failed to get list {list_id}")
                    return []

                data = await resp.json()

            items = []
            files = data.get("files", [])

            for file_obj in files:
                file_id = file_obj.get("id")
                filename = file_obj.get("name", file_id)

                if file_id:
                    download_url = f"https://pixeldrain.com/api/file/{file_id}"
                    items.append(ScrapedItem(
                        url=download_url,
                        filename=filename,
                        referer=None,
                        album=data.get("title", list_id),  # Use list title as album
                        dl_type="direct"
                    ))

            return items

        except Exception as e:
            print(f"PixelDrain: Error scraping list {list_id}: {e}")
            return []
