"""Pre-crawl discovery: build a date map from sitemaps and RSS/Atom feeds."""

import asyncio
import re
from datetime import datetime
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..config import ScrapeConfig
from ..engine.fetcher import fetch_page_http
from .dates import _parse_iso_date, _parse_rfc2822_date

# Type alias for the discovery map: url_path -> datetime
DiscoveryMap = dict[str, datetime]


def _fetch_url(url: str, config: ScrapeConfig) -> str | None:
    """Fetch a URL via HTTP, returning body text or None on error."""
    try:
        response = fetch_page_http(url, config)
        if response.status == 200:
            return response.body.decode(response.encoding or "utf-8")
    except Exception as e:
        if config.verbose:
            print(f"    Discovery fetch failed ({type(e).__name__}: {e}): {url}")
    return None


async def _fetch_url_async(url: str, config: ScrapeConfig) -> str | None:
    """Async wrapper around _fetch_url — runs sync HTTP fetch in thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_url, url, config)


def _parse_sitemap(xml_text: str, base_url: str) -> tuple[DiscoveryMap, list[str]]:
    """Parse a sitemap XML, returning (url->date map, list of sub-sitemap URLs)."""
    date_map: DiscoveryMap = {}
    sub_sitemaps: list[str] = []

    try:
        soup = BeautifulSoup(xml_text, "lxml-xml")
    except Exception:
        # Fall back to lxml html parser for malformed XML
        try:
            soup = BeautifulSoup(xml_text, "lxml")
        except Exception:
            return date_map, sub_sitemaps

    # Check for sitemap index
    for sitemap_tag in soup.find_all("sitemap"):
        loc = sitemap_tag.find("loc")
        if loc and loc.string:
            sub_sitemaps.append(loc.string.strip())

    # Parse URL entries
    parsed_base = urlparse(base_url)
    for url_tag in soup.find_all("url"):
        loc = url_tag.find("loc")
        lastmod = url_tag.find("lastmod")
        if not loc or not loc.string:
            continue

        url_str = loc.string.strip()
        parsed = urlparse(url_str)

        # Only include same-domain URLs
        if parsed.netloc and parsed.netloc != parsed_base.netloc:
            continue

        url_path = parsed.path or "/"

        if lastmod and lastmod.string:
            dt = _parse_iso_date(lastmod.string.strip())
            if dt:
                date_map[url_path] = dt

    return date_map, sub_sitemaps


def _parse_rss(xml_text: str, base_url: str) -> DiscoveryMap:
    """Parse an RSS feed, returning url_path -> pubDate map."""
    date_map: DiscoveryMap = {}

    try:
        soup = BeautifulSoup(xml_text, "lxml-xml")
    except Exception:
        try:
            soup = BeautifulSoup(xml_text, "lxml")
        except Exception:
            return date_map

    parsed_base = urlparse(base_url)

    for item in soup.find_all("item"):
        link = item.find("link")
        pubdate = item.find("pubDate")
        if not link:
            continue

        url_str = (link.string or link.get_text()).strip()
        if not url_str:
            continue

        parsed = urlparse(url_str)
        if parsed.netloc and parsed.netloc != parsed_base.netloc:
            continue

        url_path = parsed.path or "/"

        if pubdate and pubdate.string:
            dt = _parse_rfc2822_date(pubdate.string.strip())
            if dt:
                date_map[url_path] = dt

    return date_map


def _parse_atom(xml_text: str, base_url: str) -> DiscoveryMap:
    """Parse an Atom feed, returning url_path -> published map."""
    date_map: DiscoveryMap = {}

    try:
        soup = BeautifulSoup(xml_text, "lxml-xml")
    except Exception:
        try:
            soup = BeautifulSoup(xml_text, "lxml")
        except Exception:
            return date_map

    parsed_base = urlparse(base_url)

    for entry in soup.find_all("entry"):
        link = entry.find("link", attrs={"rel": "alternate"}) or entry.find("link")
        published = entry.find("published") or entry.find("updated")
        if not link:
            continue

        url_str = link.get("href", "").strip()
        if not url_str:
            continue

        # Resolve relative URLs
        if url_str.startswith("/"):
            url_str = base_url + url_str
        elif not url_str.startswith("http"):
            url_str = base_url + "/" + url_str

        parsed = urlparse(url_str)
        if parsed.netloc and parsed.netloc != parsed_base.netloc:
            continue

        url_path = parsed.path or "/"

        if published and published.string:
            dt = _parse_iso_date(published.string.strip())
            if dt:
                date_map[url_path] = dt

    return date_map


def _discover_feeds_from_html(html: str, base_url: str) -> list[str]:
    """Find RSS/Atom feed URLs from <link rel="alternate"> tags on the homepage."""
    feeds: list[str] = []

    try:
        soup = BeautifulSoup(html[:20000], "lxml")
    except Exception:
        return feeds

    for link in soup.find_all("link", attrs={"rel": "alternate"}):
        link_type = (link.get("type") or "").lower()
        if "rss" in link_type or "atom" in link_type or "xml" in link_type:
            href = link.get("href", "").strip()
            if href:
                if href.startswith("/"):
                    href = base_url + href
                elif not href.startswith("http"):
                    href = base_url + "/" + href
                feeds.append(href)

    return feeds


# Common feed paths to try if auto-discovery finds nothing
_COMMON_FEED_PATHS = [
    "/feed",
    "/feed/",
    "/rss",
    "/rss.xml",
    "/feed.xml",
    "/atom.xml",
    "/index.xml",
]


def fetch_robots_txt(config: ScrapeConfig) -> str | None:
    """Fetch /robots.txt, returning content or None on error."""
    return _fetch_url(config.base_url + "/robots.txt", config)


def parse_robots_sitemaps(robots_content: str) -> list[str]:
    """Extract Sitemap: directive URLs from robots.txt content."""
    sitemaps: list[str] = []
    for line in robots_content.splitlines():
        line = line.strip()
        if line.lower().startswith("sitemap:"):
            url = line.split(":", 1)[1].strip()
            if url:
                sitemaps.append(url)
    return sitemaps


async def run_discovery(
    config: ScrapeConfig,
    extra_sitemaps: list[str] | None = None,
) -> tuple[DiscoveryMap, str | None, list[str]]:
    """Run pre-crawl discovery: fetch sitemaps and feeds, build a URL date map.

    Fetches sitemaps and feeds concurrently for faster discovery.
    All errors are silently caught — missing sitemaps/feeds are normal.
    Returns a tuple of:
      - DiscoveryMap: dict mapping url_path -> datetime for known-dated URLs
      - homepage_html: raw HTML of the homepage (for nav parsing reuse), or None
      - discovered_feed_urls: list of RSS/Atom feed URLs found
    """
    date_map: DiscoveryMap = {}
    verbose = config.verbose

    # 1. Fetch sitemap.xml and homepage concurrently (both are independent)
    sitemap_url = config.base_url + "/sitemap.xml"
    homepage_url = config.base_url + "/"
    if verbose:
        print(f"  Discovery: fetching sitemap.xml + homepage concurrently")

    sitemap_xml, homepage_html = await asyncio.gather(
        _fetch_url_async(sitemap_url, config),
        _fetch_url_async(homepage_url, config),
    )

    # Process main sitemap
    sub_sitemaps: list[str] = []
    if sitemap_xml:
        urls_found, sub_sitemaps = _parse_sitemap(sitemap_xml, config.base_url)
        date_map.update(urls_found)
        if verbose:
            print(f"  Discovery: sitemap.xml — {len(urls_found)} dated URLs")

    # Collect all sub-sitemap URLs to fetch (from main + robots.txt extras)
    seen_sitemaps = {sitemap_url}
    all_sub_urls: list[str] = []
    for u in sub_sitemaps[:20]:
        if u not in seen_sitemaps:
            seen_sitemaps.add(u)
            all_sub_urls.append(u)

    if extra_sitemaps:
        for sm_url in extra_sitemaps:
            if sm_url not in seen_sitemaps:
                seen_sitemaps.add(sm_url)
                all_sub_urls.append(sm_url)

    # Fetch all sub-sitemaps concurrently
    if all_sub_urls:
        if verbose:
            print(f"  Discovery: fetching {len(all_sub_urls)} sub-sitemaps concurrently")
        sub_results = await asyncio.gather(
            *[_fetch_url_async(u, config) for u in all_sub_urls]
        )
        # Process results + collect second-level sub-sitemaps
        level2_urls: list[str] = []
        for sub_url, sub_xml in zip(all_sub_urls, sub_results):
            if not sub_xml:
                continue
            sub_urls, sub_subs = _parse_sitemap(sub_xml, config.base_url)
            date_map.update(sub_urls)
            if verbose:
                print(f"  Discovery: sub-sitemap — {len(sub_urls)} dated URLs")
            for u in sub_subs[:20]:
                if u not in seen_sitemaps:
                    seen_sitemaps.add(u)
                    level2_urls.append(u)

        # Fetch second-level sub-sitemaps concurrently
        if level2_urls:
            if verbose:
                print(f"  Discovery: fetching {len(level2_urls)} level-2 sub-sitemaps concurrently")
            level2_results = await asyncio.gather(
                *[_fetch_url_async(u, config) for u in level2_urls]
            )
            for sub_xml in level2_results:
                if not sub_xml:
                    continue
                sub_urls, _ = _parse_sitemap(sub_xml, config.base_url)
                date_map.update(sub_urls)
                if verbose:
                    print(f"  Discovery: level-2 sub-sitemap — {len(sub_urls)} dated URLs")

    # 2. Try RSS/Atom feeds
    # Auto-discover from homepage (already fetched above)
    feed_urls: list[str] = []
    if homepage_html:
        feed_urls = _discover_feeds_from_html(homepage_html, config.base_url)

    # Track which feeds were actually discovered (vs. guessed common paths)
    discovered_feed_urls = list(feed_urls)

    # If no feeds discovered, try common paths
    if not feed_urls:
        feed_urls = [config.base_url + path for path in _COMMON_FEED_PATHS]

    # Deduplicate feed URLs
    seen_feeds: set[str] = set()
    unique_feeds: list[str] = []
    for feed_url in feed_urls[:10]:
        if feed_url not in seen_feeds:
            seen_feeds.add(feed_url)
            unique_feeds.append(feed_url)

    # Fetch all feeds concurrently
    if unique_feeds:
        if verbose:
            print(f"  Discovery: fetching {len(unique_feeds)} feeds concurrently")
        feed_results = await asyncio.gather(
            *[_fetch_url_async(u, config) for u in unique_feeds]
        )
        for feed_url, feed_xml in zip(unique_feeds, feed_results):
            if not feed_xml:
                continue
            # Detect RSS vs Atom
            if "<rss" in feed_xml[:500] or "<channel" in feed_xml[:500]:
                feed_dates = _parse_rss(feed_xml, config.base_url)
            elif "<feed" in feed_xml[:500]:
                feed_dates = _parse_atom(feed_xml, config.base_url)
            else:
                continue

            # If this was a guessed URL that worked, add to discovered list
            if feed_url not in discovered_feed_urls and feed_dates:
                discovered_feed_urls.append(feed_url)

            date_map.update(feed_dates)
            if verbose and feed_dates:
                print(f"  Discovery: feed — {len(feed_dates)} dated URLs")

    if verbose:
        print(f"  Discovery complete: {len(date_map)} total dated URLs")

    return date_map, homepage_html, discovered_feed_urls
