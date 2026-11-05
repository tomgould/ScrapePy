"""Audit mode, site reconnaissance + test fixture generation."""

import asyncio
import json
import os
import re
from datetime import datetime, timezone

from ..config import ScrapeConfig
from ..engine.fetcher import fetch_page_http
from ..processing.discovery import (
    DiscoveryMap,
    load_robots_policy,
    parse_robots_sitemaps,
    run_discovery,
)
from ..processing.navigation import extract_navigation
from ..processing.outline import build_site_outline, SiteOutline
from ..processing.urls import discover_section_pages, has_article_slug
from ..processing.classifier import classify_page, PageType
from ..processing.dates import get_best_date
from ..processing.robots import HostRateLimiter


def _sanitize_filename(path: str) -> str:
    """Convert a URL path to a safe filename for fixture storage.

    /f1/news -> section_f1_news.html
    /f1/news/123/verstappen-wins -> article_verstappen-wins.html
    """
    clean = path.strip("/")
    if not clean:
        return "homepage.html"

    # Use last segment as the main name
    last_segment = clean.rsplit("/", 1)[-1]
    # Sanitize: keep alphanumeric, hyphens, underscores
    safe = re.sub(r'[^a-zA-Z0-9_-]', '_', last_segment)

    if has_article_slug(path):
        return f"article_{safe}.html"
    else:
        # Use full path for sections to avoid collisions
        safe_full = re.sub(r'[^a-zA-Z0-9_-]', '_', clean.replace("/", "_"))
        return f"section_{safe_full}.html"


def _fetch_sample_page(url: str, config: ScrapeConfig) -> tuple[str, int]:
    """Fetch a page via HTTP, returning (html, status_code).

    Returns status 0 on fetch failure (not a server response).
    """
    try:
        response = fetch_page_http(url, config)
        status = response.status
        if status == 200:
            html = response.body.decode(response.encoding or "utf-8")
            return html, status
        return "", status
    except Exception as e:
        print(f"    Fetch failed ({type(e).__name__}: {e})")
        return "", 0


async def run_audit_mode(config: ScrapeConfig):
    """Run site reconnaissance and save fixtures for test validation."""
    verbose = config.verbose
    fixture_dir = os.path.join("tests", "fixtures", config.output_dir_name)
    pages_dir = os.path.join(fixture_dir, "pages")
    meta_dir = os.path.join(fixture_dir, "meta")

    os.makedirs(pages_dir, exist_ok=True)
    os.makedirs(meta_dir, exist_ok=True)

    # Collect audit results
    audit_data: dict = {
        "url": config.url,
        "domain": config.domain,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # ---------------------------------------------------------------
    # Phase 1: Recon (same as crawl.py Phase 0)
    # ---------------------------------------------------------------
    print("Phase 1: Site reconnaissance...")

    # 1a. Fetch robots.txt
    robots, robots_content = load_robots_policy(config)
    if robots.enforced and robots.disallow_all:
        print(f"  {robots.note}. Sample pages will not be fetched "
              "(use --ignore-robots only if you have permission).")
    robots_sitemaps: list[str] = []
    if robots_content:
        robots_sitemaps = parse_robots_sitemaps(robots_content)
        # Save raw robots.txt
        robots_path = os.path.join(meta_dir, "robots.txt")
        with open(robots_path, "w", encoding="utf-8") as f:
            f.write(robots_content)
        if verbose:
            print(f"  Saved robots.txt ({len(robots_sitemaps)} sitemaps found)")
    else:
        if verbose:
            print("  No robots.txt found")

    audit_data["robots_sitemaps"] = robots_sitemaps

    # 1b. Run discovery (sitemaps + RSS + homepage)
    discovery_map, homepage_html, feed_urls = await run_discovery(
        config, extra_sitemaps=robots_sitemaps,
        robots=robots, limiter=HostRateLimiter(robots.min_delay(1.0)),
    )
    audit_data["discovery_urls"] = len(discovery_map)
    audit_data["feed_urls"] = feed_urls

    if discovery_map:
        print(f"  Found {len(discovery_map)} dated URLs from sitemaps/feeds")
    else:
        print("  No dated URLs found in sitemaps/feeds")

    # 1c. Parse homepage navigation
    nav_links = []
    if homepage_html:
        nav_links = extract_navigation(homepage_html, config.base_url)
        print(f"  Found {len(nav_links)} navigation links")

        # Save homepage HTML
        homepage_path = os.path.join(pages_dir, "homepage.html")
        with open(homepage_path, "w", encoding="utf-8") as f:
            f.write(homepage_html)
        if verbose:
            print(f"  Saved homepage HTML ({len(homepage_html):,} bytes)")
    else:
        print("  Could not fetch homepage")

    # 1d. Build outline
    outline: SiteOutline | None = None
    if nav_links or discovery_map:
        outline = build_site_outline(
            nav_links, discovery_map, feed_urls, robots_sitemaps, config,
        )
        print(f"  Site outline: {len(outline.nav_sections)} sections, {outline.sitemap_url_count} sitemap URLs")

    # 1e. Discover section pages from all homepage links
    section_pages: list[str] = []
    if homepage_html:
        section_pages = discover_section_pages(homepage_html, config.base_url)
        print(f"  Section pages discovered: {len(section_pages)}")

    audit_data["nav_link_count"] = len(nav_links)
    audit_data["nav_labels"] = [l.label for l in nav_links if l.level == 0 and l.source != "footer"]
    audit_data["section_pages"] = section_pages
    if outline:
        audit_data["outline"] = json.loads(outline.to_json())

    print()

    # ---------------------------------------------------------------
    # Phase 2: Sample pages
    # ---------------------------------------------------------------
    print("Phase 2: Fetching sample pages...")

    sample_pages: list[dict] = []

    # Homepage (already fetched, classify it)
    if homepage_html:
        page_type = classify_page("/", homepage_html)
        date_result = get_best_date("/", homepage_html)
        sample_pages.append({
            "file": "pages/homepage.html",
            "path": "/",
            "url": config.base_url + "/",
            "type": page_type.value,
            "date": date_result.date.isoformat() if date_result else None,
            "date_source": date_result.source.name if date_result else None,
        })
        print(f"  Homepage: {page_type.value}")

    # Section pages: first 3 from outline
    section_paths: list[str] = []
    if outline:
        for section in outline.nav_sections[:3]:
            section_paths.append(section.path)

    sample_delay = robots.min_delay(1.0)
    for path in section_paths:
        full_url = config.base_url + path
        if not robots.can_fetch(full_url):
            print(f"  Skipped (robots.txt): {full_url}")
            continue
        print(f"  Fetching section: {full_url}")
        html, status = _fetch_sample_page(full_url, config)
        if not html or status != 200:
            print(f"    Skipped (HTTP {status})")
            continue

        # Wait between fetches (robots.txt Crawl-delay raises this)
        await asyncio.sleep(sample_delay)

        filename = _sanitize_filename(path)
        filepath = os.path.join(pages_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        page_type = classify_page(path, html)
        date_result = get_best_date(path, html)

        # Count links on section page
        link_count = len(re.findall(r'href=["\']([^"\']+)["\']', html))

        sample_pages.append({
            "file": f"pages/{filename}",
            "path": path,
            "url": full_url,
            "type": page_type.value,
            "date": date_result.date.isoformat() if date_result else None,
            "date_source": date_result.source.name if date_result else None,
            "link_count": link_count,
        })
        print(f"    {page_type.value}, {link_count} links")

    # Article pages: newest 3 from discovery_map with article-like slugs
    article_candidates: list[tuple[str, datetime]] = []
    for url_path, dt in discovery_map.items():
        if has_article_slug(url_path):
            article_candidates.append((url_path, dt))

    # Sort by date descending (newest first)
    article_candidates.sort(key=lambda x: x[1], reverse=True)

    article_candidates = [
        (p, dt) for p, dt in article_candidates if robots.can_fetch(config.base_url + p)
    ]
    for url_path, _ in article_candidates[:3]:
        full_url = config.base_url + url_path
        print(f"  Fetching article: {full_url}")
        html, status = _fetch_sample_page(full_url, config)
        if not html or status != 200:
            print(f"    Skipped (HTTP {status})")
            continue

        await asyncio.sleep(sample_delay)

        filename = _sanitize_filename(url_path)
        filepath = os.path.join(pages_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        page_type = classify_page(url_path, html)
        date_result = get_best_date(url_path, html)

        sample_pages.append({
            "file": f"pages/{filename}",
            "path": url_path,
            "url": full_url,
            "type": page_type.value,
            "date": date_result.date.isoformat() if date_result else None,
            "date_source": date_result.source.name if date_result else None,
        })
        date_str = date_result.date.strftime("%Y-%m-%d") if date_result else "no date"
        print(f"    {page_type.value}, published {date_str}")

    audit_data["sample_pages"] = sample_pages

    print()

    # ---------------------------------------------------------------
    # Phase 3: Save results
    # ---------------------------------------------------------------
    print("Phase 3: Saving results...")

    # audit_report.json, full results
    report_path = os.path.join(fixture_dir, "audit_report.json")
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(audit_data, f, indent=2, ensure_ascii=False, default=str)
        print(f"  Saved: {report_path}")
    except OSError as e:
        print(f"  Error saving audit report: {e}")
        return

    # test_config.json, committable expected values for test validation
    test_config = _build_test_config(config, outline, sample_pages, robots_content, section_pages)
    test_config_path = os.path.join(fixture_dir, "test_config.json")
    try:
        with open(test_config_path, "w", encoding="utf-8") as f:
            json.dump(test_config, f, indent=2, ensure_ascii=False)
        print(f"  Saved: {test_config_path}")
    except OSError as e:
        print(f"  Error saving test config: {e}")

    # Summary
    print()
    print("--- Audit Complete ---")
    print(f"Fixture directory: {fixture_dir}")
    file_count = sum(1 for _ in _walk_files(fixture_dir))
    print(f"Files created: {file_count}")
    if outline:
        print(f"Nav sections: {len(outline.nav_sections)}")
    if section_pages:
        print(f"Section pages: {len(section_pages)}")
    print(f"Sample pages: {len(sample_pages)}")
    print(f"Discovery URLs: {len(discovery_map)}")
    if robots_sitemaps:
        print(f"Sitemaps in robots.txt: {len(robots_sitemaps)}")
    if feed_urls:
        print(f"RSS/Atom feeds: {len(feed_urls)}")
    print()
    print(f"Run tests: python -m pytest tests/test_audit_fixtures.py -v")


def _build_test_config(
    config: ScrapeConfig,
    outline: SiteOutline | None,
    sample_pages: list[dict],
    robots_content: str | None,
    section_pages: list[str] | None = None,
) -> dict:
    """Build the test_config.json with expected values for fixture validation."""
    tc: dict = {
        "url": config.url,
        "domain": config.output_dir_name,
    }

    # Navigation expectations
    homepage_entry = next((p for p in sample_pages if p["path"] == "/"), None)
    if homepage_entry and outline:
        section_labels = [s.label for s in outline.nav_sections]
        tc["navigation"] = {
            "file": homepage_entry["file"],
            "min_links": max(1, len(section_labels)),
            "expected_sections": section_labels[:10],
        }

    # Page expectations
    tc["pages"] = []
    for page in sample_pages:
        if page["path"] == "/":
            continue  # Homepage handled in navigation
        entry: dict = {
            "file": page["file"],
            "path": page["path"],
            "expected_type": page["type"],
        }
        if page.get("date"):
            # Store just the date portion for comparison
            entry["expected_date"] = page["date"][:10]
        if page.get("link_count"):
            entry["min_links"] = max(1, page["link_count"] // 2)
        tc["pages"].append(entry)

    # Section pages discovered from homepage
    if section_pages:
        tc["section_pages"] = section_pages

    # Discovery expectations
    tc["discovery"] = {}
    if robots_content:
        tc["discovery"]["robots_file"] = "meta/robots.txt"
        sitemaps = parse_robots_sitemaps(robots_content)
        tc["discovery"]["min_sitemaps"] = len(sitemaps)

    return tc


def _walk_files(directory: str):
    """Yield all file paths in a directory tree."""
    for root, _dirs, files in os.walk(directory):
        for f in files:
            yield os.path.join(root, f)
