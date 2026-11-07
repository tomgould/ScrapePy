"""Single page mode (-1): one page with full JavaScript rendering, no crawling.

The page goes through the crawl's page pipeline (modes/crawl.py) with only
its own URL queued, so it gets the same file naming, frontmatter, incremental
cache and run summary as a crawled page. What differs is the fetch: the
browser waits for the page to render and scrolls it to load lazy content.
"""

import asyncio
import dataclasses

from ..config import ScrapeConfig
from ..engine.browser_actions import scroll_full_page
from ..processing.robots import RobotsPolicy


async def run_single_page_mode(config: ScrapeConfig, robots: RobotsPolicy | None = None) -> dict:
    """Fetch, save and convert config.url; returns the run summary."""
    print(f"Fetching single page: {config.url}")

    async def page_action(page):
        print("  Page loaded, waiting for content to render...")
        await asyncio.sleep(config.wait_time)

        print("  Scrolling to load all content...")
        await scroll_full_page(page)

        # Wait for lazy-loaded content
        await asyncio.sleep(3)

        return page

    # A date filter has no effect here: the page is saved whatever its date
    if config.has_date_filter:
        config = dataclasses.replace(config, recent=None, since=None,
                                     min_articles=0, max_articles=0)

    from .crawl import run_crawl_mode
    return await run_crawl_mode(config, robots=robots, page_action=page_action)
