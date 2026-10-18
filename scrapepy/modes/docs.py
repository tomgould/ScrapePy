"""Documentation site mode, navigates sidebar links and captures all pages."""

import asyncio
import html as html_lib
import os
import re
from urllib.parse import urlparse

from ..config import ScrapeConfig
from ..engine.fetcher import fetch_page_browser
from ..engine.browser_actions import (
    find_nav_links,
    expand_docs_sections,
    scroll_full_page,
    get_main_content_html,
)
from ..output.writer import write_file


async def run_docs_mode(config: ScrapeConfig):
    print(f"Fetching documentation site: {config.url}")

    all_content: list[dict] = []
    visited_urls: set[str] = set()
    visited_urls.add(config.url)

    async def initial_page_action(page):
        print("  Page loaded, waiting for content to render...")
        await asyncio.sleep(config.wait_time)
        return page

    # First load: get the main page and find nav links
    try:
        response = await fetch_page_browser(
            config.url, config, page_action=initial_page_action
        )
    except Exception as e:
        print(f"\nError: Failed to load page ({type(e).__name__}: {e})")
        print("Check that the URL is correct and the browser is installed (scrapling install).")
        return

    # For docs mode we need direct browser access, use StealthyFetcher differently
    # We'll use a simpler approach: fetch each page individually
    print("  Finding navigation links...")

    # Get the initial page HTML
    main_html = response.body.decode(response.encoding or "utf-8")
    all_content.append({
        "url": config.url,
        "title": "Home",
        "content": main_html,
    })

    # Extract nav links from HTML
    nav_links = _extract_nav_links(main_html, config.base_url)
    print(f"  Found {len(nav_links)} navigation links")

    # Visit each nav link
    for i, link in enumerate(nav_links):
        if link["href"] in visited_urls:
            continue
        visited_urls.add(link["href"])

        short_text = link["text"][:40]
        print(f"  [{i + 1}/{len(nav_links)}] Loading: {short_text}...")

        try:
            async def sub_page_action(page):
                await asyncio.sleep(1.5)
                await expand_docs_sections(page)
                await scroll_full_page(page)
                return page

            sub_response = await fetch_page_browser(
                link["href"], config, page_action=sub_page_action, wait_time=1.0
            )

            page_html = sub_response.body.decode(sub_response.encoding or "utf-8")
            page_title = _extract_title(page_html) or link["text"]

            all_content.append({
                "url": link["href"],
                "title": page_title,
                "nav_text": link["text"],
                "content": page_html,
            })

        except Exception as e:
            print(f"    Warning: Could not load {link['href']}: {e}")

    print(f"\n  Captured {len(all_content)} pages")

    # Save main page
    main_output = os.path.join(config.output_dir, "index.html")
    write_file(main_output, main_html)
    print(f"  Saved main page: {main_output}")

    # Save each page as separate HTML
    for item in all_content:
        parsed_url = urlparse(item["url"])
        filename = parsed_url.path.lstrip("/").replace("/", "_") or "index"
        if not filename.endswith(".html"):
            filename += ".html"

        nav_text = item.get("nav_text", item["title"])
        if item["content"].strip().startswith("<!DOCTYPE") or item["content"].strip().startswith("<html"):
            full_html = item["content"]
        else:
            safe_title = html_lib.escape(item['title'])
            safe_nav = html_lib.escape(nav_text)
            full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{safe_title}</title>
</head>
<body>
    <h1>{safe_nav}</h1>
    {item['content']}
</body>
</html>"""

        write_file(os.path.join(config.output_dir, filename), full_html)

    # Create combined document
    combined_parts = [
        '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="UTF-8">\n'
        '<title>Combined Documentation</title>\n</head>\n<body>\n'
    ]
    for item in all_content:
        nav_text = item.get("nav_text", item["title"])
        section_id = re.sub(r'\s+', '-', nav_text.lower()) if nav_text else "section"
        safe_section_id = html_lib.escape(section_id)
        safe_nav = html_lib.escape(nav_text)
        # Extract just the body content if it's a full HTML document
        content = _extract_body_content(item["content"])
        combined_parts.append(
            f'<section id="{safe_section_id}">\n'
            f'<h1>{safe_nav}</h1>\n'
            f'{content}\n'
            f'</section>\n<hr>\n'
        )
    combined_parts.append("</body></html>")

    write_file(os.path.join(config.output_dir, "combined.html"), "".join(combined_parts))
    print("  Saved combined document: combined.html")

    print("\n--- Scraping Complete ---")
    print(f"Pages saved: {len(all_content)}")
    print(f"Output folder: {config.output_dir}")


def _extract_nav_links(html: str, base_url: str) -> list[dict]:
    """Extract navigation links from HTML."""
    links = []
    seen = set()

    # Find links within nav, sidebar, menu elements
    # Use regex since we don't have the browser DOM
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>', html, re.IGNORECASE):
        href = m.group(1)
        text = m.group(2).strip()

        if not text or len(text) > 100:
            continue

        # Resolve relative URLs
        if href.startswith("/"):
            href = base_url + href
        elif not href.startswith("http"):
            continue

        if not href.startswith(base_url):
            continue

        if href in seen:
            continue
        seen.add(href)

        links.append({"href": href, "text": text})

    return links


def _extract_title(html: str) -> str:
    """Extract title from HTML."""
    m = re.search(r'<title>([^<]+)</title>', html, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _extract_body_content(html: str) -> str:
    """Extract body content from a full HTML document."""
    m = re.search(r'<body[^>]*>(.*)</body>', html, re.IGNORECASE | re.DOTALL)
    return m.group(1) if m else html
