"""
Fetchr Reddit Scraper
Handles Reddit posts with images, videos, galleries, and external links
"""
import aiohttp
import html
from typing import List, Dict, Optional
from .base import BaseScraper, ScrapedItem


class RedditScraper(BaseScraper):
    DOMAINS = ["reddit.com", "old.reddit.com", "www.reddit.com"]

    async def scrape(self, url: str, cookies: str = "") -> List[ScrapedItem]:
        """
        Scrape Reddit post using JSON API.
        Handles:
        - Direct Reddit images/videos
        - Galleries (media_metadata)
        - External links (imgur, gfycat, etc.)
        """
        try:
            # Normalize URL and append .json
            json_url = url.rstrip("/") + ".json"

            headers = {
                "User-Agent": "Fetchr/1.0 (compatible with Reddit API)"
            }
            if cookies:
                headers["Cookie"] = cookies

            async with aiohttp.ClientSession() as session:
                async with session.get(json_url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        print(f"Reddit: Failed to fetch {url} (status {resp.status})")
                        return []

                    data = await resp.json()

            items = []

            # Parse the JSON structure
            if isinstance(data, list) and len(data) > 0:
                post_data = data[0].get("data", {}).get("children", [])
                if post_data:
                    post = post_data[0].get("data", {})
                    items = await self._parse_post(post, url)

            print(f"Reddit: Found {len(items)} items from {url}")
            return items

        except Exception as e:
            print(f"Reddit scraper error: {e}")
            return []

    async def _parse_post(self, post: Dict, post_url: str) -> List[ScrapedItem]:
        """Parse a Reddit post and extract media."""
        items = []

        # Get post title for album name
        title = post.get("title", "Reddit")

        # Check post_hint to determine media type
        post_hint = post.get("post_hint", "")

        # Strategy 1: Gallery (media_metadata)
        if post_hint == "gallery":
            gallery_items = await self._parse_gallery(post, title, post_url)
            items.extend(gallery_items)

        # Strategy 2: Video
        elif post_hint == "video":
            video_items = await self._parse_video(post, title, post_url)
            items.extend(video_items)

        # Strategy 3: Image
        elif post_hint == "image":
            image_items = await self._parse_image(post, title, post_url)
            items.extend(image_items)

        # Strategy 4: Rich media (iframe)
        elif post_hint == "rich:video":
            external_url = post.get("url", "")
            if external_url:
                items.append(ScrapedItem(
                    url=external_url,
                    filename=None,
                    referer=post_url,
                    album=title,
                    dl_type="external"
                ))

        # Strategy 5: Default - check URL field
        else:
            url = post.get("url", "")
            if url and url.startswith("http"):
                # This is likely a link to external content
                items.append(ScrapedItem(
                    url=url,
                    filename=None,
                    referer=post_url,
                    album=title,
                    dl_type="external"
                ))

        return items

    async def _parse_gallery(self, post: Dict, title: str, post_url: str) -> List[ScrapedItem]:
        """Parse a Reddit gallery (multiple images/videos)."""
        items = []

        try:
            media_metadata = post.get("media_metadata", {})

            for item_id, item_data in media_metadata.items():
                if isinstance(item_data, dict):
                    # Check if it's an image
                    if item_data.get("type") == "image":
                        # URL is in .s.u (decoded from HTML entities)
                        s_data = item_data.get("s", {})
                        url = s_data.get("u", "")
                        if url:
                            # Decode HTML entities
                            url = html.unescape(url)
                            items.append(ScrapedItem(
                                url=url,
                                filename=None,
                                referer=post_url,
                                album=title,
                                dl_type="direct"
                            ))

                    # Check if it's a video
                    elif item_data.get("type") == "video":
                        s_data = item_data.get("s", {})
                        url = s_data.get("u", "")
                        if url:
                            url = html.unescape(url)
                            items.append(ScrapedItem(
                                url=url,
                                filename=None,
                                referer=post_url,
                                album=title,
                                dl_type="direct"
                            ))

        except Exception as e:
            print(f"Reddit: Error parsing gallery: {e}")

        return items

    async def _parse_video(self, post: Dict, title: str, post_url: str) -> List[ScrapedItem]:
        """Parse a Reddit video post."""
        items = []

        try:
            # Get video URL from media.reddit_video or secure_media.reddit_video
            video_url = None
            audio_url = None

            # Try media.reddit_video first
            media = post.get("media", {})
            reddit_video = media.get("reddit_video", {})

            if reddit_video:
                video_url = reddit_video.get("fallback_url")
                # Audio is at base URL with DASH_audio.mp4
                base_url = reddit_video.get("fallback_url", "").rsplit("/", 1)[0]
                if base_url:
                    audio_url = base_url + "/DASH_audio.mp4"

            # Fallback to secure_media
            if not video_url:
                secure_media = post.get("secure_media", {})
                reddit_video = secure_media.get("reddit_video", {})
                if reddit_video:
                    video_url = reddit_video.get("fallback_url")
                    base_url = reddit_video.get("fallback_url", "").rsplit("/", 1)[0]
                    if base_url:
                        audio_url = base_url + "/DASH_audio.mp4"

            if video_url:
                items.append(ScrapedItem(
                    url=video_url,
                    filename="video.mp4",
                    referer=post_url,
                    album=title,
                    dl_type="direct"
                ))

            if audio_url:
                items.append(ScrapedItem(
                    url=audio_url,
                    filename="audio.mp4",
                    referer=post_url,
                    album=title,
                    dl_type="direct"
                ))

        except Exception as e:
            print(f"Reddit: Error parsing video: {e}")

        return items

    async def _parse_image(self, post: Dict, title: str, post_url: str) -> List[ScrapedItem]:
        """Parse a Reddit single image post."""
        items = []

        try:
            # Get image URL from preview or media
            image_url = None

            # Try preview images first
            preview = post.get("preview", {})
            images = preview.get("images", [])
            if images:
                image_data = images[0].get("source", {})
                image_url = image_data.get("url")
                if image_url:
                    image_url = html.unescape(image_url)

            # Try media
            if not image_url:
                media = post.get("media", {})
                oembed = media.get("oembed", {})
                image_url = oembed.get("thumbnail_url")

            # Fallback to post URL
            if not image_url:
                image_url = post.get("url", "")

            if image_url and image_url.startswith("http"):
                items.append(ScrapedItem(
                    url=image_url,
                    filename=None,
                    referer=post_url,
                    album=title,
                    dl_type="direct"
                ))

        except Exception as e:
            print(f"Reddit: Error parsing image: {e}")

        return items
