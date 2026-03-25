"""
Fetchr Kemono.su Scraper
Handles kemono.su and kemono.party user profiles and individual posts.
Uses the public JSON API to fetch posts and attachments.
"""
import aiohttp
import asyncio
from typing import List, Optional
from .base import BaseScraper, ScrapedItem


class KemonoScraper(BaseScraper):
    DOMAINS = ["kemono.su", "kemono.party"]
    API_BASE = "https://kemono.su/api/v1"
    CDN_PRIMARY = "https://c3.kemono.su"
    CDN_FALLBACK = "https://c2.kemono.su"
    POSTS_PER_PAGE = 50

    async def scrape(self, url: str, cookies: str = "") -> List[ScrapedItem]:
        """
        Scrape Kemono URLs.
        Handles:
        - https://kemono.su/{service}/user/{user_id}
        - https://kemono.su/{service}/user/{user_id}/post/{post_id}
        """
        try:
            headers = self._build_headers(cookies=cookies, referer=url)
            items = []

            # Parse URL to extract service and user_id
            path_parts = url.rstrip("/").split("/")
            
            # Find indices of 'user' and 'post'
            if "user" not in path_parts:
                return []
            
            user_idx = path_parts.index("user")
            if user_idx + 1 >= len(path_parts):
                return []
            
            service = path_parts[user_idx - 1] if user_idx > 0 else None
            user_id = path_parts[user_idx + 1]
            
            if not service or not user_id:
                return []
            
            # Check if this is a single post or user profile
            if "post" in path_parts:
                post_idx = path_parts.index("post")
                if post_idx + 1 < len(path_parts):
                    post_id = path_parts[post_idx + 1]
                    items = await self._scrape_single_post(
                        service, user_id, post_id, headers
                    )
            else:
                # User profile - paginate through posts
                items = await self._scrape_user_posts(
                    service, user_id, headers
                )

            print(f"Kemono: Found {len(items)} items from {url}")
            return items

        except Exception as e:
            print(f"Kemono scraper error: {e}")
            return []

    async def _scrape_single_post(
        self,
        service: str,
        user_id: str,
        post_id: str,
        headers: dict
    ) -> List[ScrapedItem]:
        """Fetch a single post from Kemono API."""
        try:
            api_url = f"{self.API_BASE}/{service}/user/{user_id}/post/{post_id}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    api_url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        return []
                    post = await resp.json()

            return self._extract_items_from_post(post)

        except Exception as e:
            print(f"Kemono: Error fetching single post: {e}")
            return []

    async def _scrape_user_posts(
        self,
        service: str,
        user_id: str,
        headers: dict
    ) -> List[ScrapedItem]:
        """Paginate through all posts for a user."""
        items = []
        offset = 0

        try:
            async with aiohttp.ClientSession() as session:
                while True:
                    api_url = (
                        f"{self.API_BASE}/{service}/user/{user_id}/posts"
                        f"?o={offset}"
                    )

                    async with session.get(
                        api_url,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as resp:
                        if resp.status != 200:
                            break

                        posts = await resp.json()
                        if not posts:
                            break

                        for post in posts:
                            items.extend(self._extract_items_from_post(post))

                        offset += self.POSTS_PER_PAGE
                        await asyncio.sleep(0.5)  # Rate limit

            return items

        except Exception as e:
            print(f"Kemono: Error fetching user posts: {e}")
            return items

    def _extract_items_from_post(self, post: dict) -> List[ScrapedItem]:
        """Extract downloadable items from a post object."""
        items = []
        album_name = post.get("title", "post")
        post_id = post.get("id", "unknown")

        # Process attachments
        attachments = post.get("attachments", [])
        for att in attachments:
            item = self._create_item_from_attachment(att, album_name)
            if item:
                items.append(item)

        # Process main file if present
        file_obj = post.get("file")
        if file_obj and file_obj.get("path"):
            item = self._create_item_from_file(file_obj, album_name)
            if item:
                items.append(item)

        return items

    def _create_item_from_attachment(
        self,
        att: dict,
        album_name: str
    ) -> Optional[ScrapedItem]:
        """Create a ScrapedItem from an attachment dict."""
        try:
            path = att.get("path", "")
            name = att.get("name", "")
            
            if not path:
                return None

            # Build CDN URL from path like /data/XX/XX/hash.ext
            url = f"{self.CDN_PRIMARY}{path}"
            
            return ScrapedItem(
                url=url,
                filename=name if name else self._extract_filename(path),
                album=album_name,
                dl_type="direct"
            )
        except Exception as e:
            print(f"Kemono: Error creating attachment item: {e}")
            return None

    def _create_item_from_file(
        self,
        file_obj: dict,
        album_name: str
    ) -> Optional[ScrapedItem]:
        """Create a ScrapedItem from a file dict."""
        try:
            path = file_obj.get("path", "")
            name = file_obj.get("name", "")
            
            if not path:
                return None

            url = f"{self.CDN_PRIMARY}{path}"
            
            return ScrapedItem(
                url=url,
                filename=name if name else self._extract_filename(path),
                album=album_name,
                dl_type="direct"
            )
        except Exception as e:
            print(f"Kemono: Error creating file item: {e}")
            return None

    @staticmethod
    def _extract_filename(path: str) -> str:
        """Extract filename from a path like /data/XX/XX/hash.ext"""
        parts = path.split("/")
        return parts[-1] if parts else "file"
