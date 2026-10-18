"""Single page SPA mode, scrape a single page with full JS rendering."""

import asyncio
import os

from ..config import ScrapeConfig
from ..engine.fetcher import fetch_page_browser
from ..engine.browser_actions import scroll_full_page
from ..output.writer import write_file


async def run_single_page_mode(config: ScrapeConfig):
    print(f"Fetching single page: {config.url}")

    async def page_action(page):
        print("  Page loaded, waiting for content to render...")
        await asyncio.sleep(config.wait_time)

        print("  Scrolling to load all content...")
        await scroll_full_page(page)

        # Wait for lazy-loaded content
        await asyncio.sleep(3)

        return page

    try:
        response = await fetch_page_browser(config.url, config, page_action=page_action)
    except Exception as e:
        print(f"\nError: Failed to load page ({type(e).__name__}: {e})")
        print("Check that the URL is correct and the browser is installed (scrapling install).")
        return

    html = response.body.decode(response.encoding or "utf-8")

    output_file = os.path.join(config.output_dir, "index.html")
    write_file(output_file, html)
    print(f"  Saved: {output_file}")
    print(f"  File size: {len(html) / 1024:.1f} KB")

    print("\n--- Scraping Complete ---")
    print("Pages saved: 1")
    print(f"Output folder: {config.output_dir}")
