"""
Fetchr GoFile Scraper
Handles GoFile content links via API
"""
import aiohttp
import asyncio
from typing import List, Optional, Dict
from .base import BaseScraper, ScrapedItem


class GoFileScraper(BaseScraper):
    DOMAINS = ["gofile.io"]
    _guest_token: Optional[str] = None

    async def _get_guest_token(self, session: aiohttp.ClientSession) -> Optional[str]:
        """Get or reuse a guest token for GoFile API."""
        if self._guest_token:
            return self._guest_token

        try:
            async with session.post(
                "https://api.gofile.io/accounts",
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "ok":
                        token = data.get("data", {}).get("token")
                        if token:
                            self._guest_token = token
                            return token
        except Exception as e:
            print(f"GoFile: Error getting guest token: {e}")

        return None

    async def _fetch_content(
        self,
        session: aiohttp.ClientSession,
        content_id: str,
        token: str
    ) -> Optional[Dict]:
        """Fetch content metadata from GoFile API."""
        try:
            url = f"https://api.gofile.io/contents/{content_id}?token={token}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "ok":
                        return data.get("data")
        except Exception as e:
            print(f"GoFile: Error fetching content {content_id}: {e}")

        return None

    async def _process_folder(
        self,
        session: aiohttp.ClientSession,
        content_data: Dict,
        token: str,
        parent_album: Optional[str] = None
    ) -> List[ScrapedItem]:
        """
        Recursively process folder content.
        Returns list of ScrapedItem for all files (including nested).
        """
        items = []

        children = content_data.get("children", {})
        if isinstance(children, dict):
            for child_id, child_data in children.items():
                child_type = child_data.get("type", "")
                child_name = child_data.get("name", child_id)

                if child_type == "file":
                    # Extract direct link
                    direct_link = child_data.get("directLink")
                    if direct_link:
                        album_name = parent_album or content_data.get("name", "GoFile")
                        items.append(ScrapedItem(
                            url=direct_link,
                            filename=child_name,
                            referer=None,
                            album=album_name,
                            dl_type="direct"
                        ))

                elif child_type == "folder":
                    # Recursively fetch nested folder
                    nested_data = await self._fetch_content(session, child_id, token)
                    if nested_data:
                        nested_album = f"{parent_album or content_data.get('name', 'GoFile')}/{child_name}"
                        nested_items = await self._process_folder(
                            session,
                            nested_data,
                            token,
                            parent_album=nested_album
                        )
                        items.extend(nested_items)

        return items

    async def scrape(self, url: str, cookies: str = "") -> List[ScrapedItem]:
        """
        Scrape GoFile content using the public API.
        Handles nested folders recursively.
        """
        try:
            # Extract content ID from URL
            # Format: https://gofile.io/d/{content_id}
            parts = url.rstrip("/").split("/")
            if len(parts) < 2:
                print(f"GoFile: Invalid URL format: {url}")
                return []

            content_id = parts[-1]

            async with aiohttp.ClientSession() as session:
                # Get guest token
                token = await self._get_guest_token(session)
                if not token:
                    print("GoFile: Failed to obtain guest token")
                    return []

                # Fetch root content
                content_data = await self._fetch_content(session, content_id, token)
                if not content_data:
                    print(f"GoFile: Failed to fetch content {content_id}")
                    return []

                # Process folder and extract all items
                items = await self._process_folder(session, content_data, token)

            print(f"GoFile: Found {len(items)} items from {url}")
            return items

        except Exception as e:
            print(f"GoFile scraper error: {e}")
            return []
