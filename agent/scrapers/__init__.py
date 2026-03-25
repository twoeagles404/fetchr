"""
Fetchr Scraper Registry
Automatically routes URLs to the correct site scraper.
Add new scrapers by importing and appending to SCRAPERS.
"""
from .cyberdrop import CyberdropScraper
from .bunkr import BunkrScraper
from .gofile import GoFileScraper
from .pixeldrain import PixelDrainScraper
from .imgur import ImgurScraper
from .reddit import RedditScraper

SCRAPERS = [
    CyberdropScraper,
    BunkrScraper,
    GoFileScraper,
    PixelDrainScraper,
    ImgurScraper,
    RedditScraper,
]


def find_scraper(url: str):
    """Find the appropriate scraper for a given URL."""
    for cls in SCRAPERS:
        if cls.can_handle(url):
            return cls()
    return None


async def scrape(url: str, cookies: str = "") -> list:
    """
    Find and run the appropriate scraper for a URL.
    Returns a list of ScrapedItem objects.
    """
    scraper = find_scraper(url)
    if not scraper:
        return []
    return await scraper.scrape(url, cookies=cookies)
