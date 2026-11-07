"""Multi-page recursive crawl mode (default) with BFS loop."""

import asyncio
import json
import itertools
import os
import re
import posixpath
from collections import deque
from datetime import datetime
from urllib.parse import urljoin, urlparse

from .. import interrupts
from ..config import ScrapeConfig
from ..proxy import redact
from ..engine.fetcher import fetch_page_http, fetch_page_browser, create_asset_client
from ..engine.threads import run_blocking
from ..engine.browser_actions import (
    wait_for_js_content,
    extract_links_browser,
)
from bs4 import BeautifulSoup

from ..processing.errors import is_error_page_html, is_error_page_content, is_cloudflare_challenge
from ..processing.assets import discover_and_download_assets
from ..processing.rewriter import rewrite_urls
from ..processing.urls import (
    is_in_scope, SKIP_EXTENSIONS, discover_section_pages,
    has_article_slug, NON_CONTENT_PATHS, is_non_section_path,
)

# Precompiled regexes for hot-path link extraction
_RE_HREF = re.compile(r'href=["\']([^"\']+)["\']')
_RE_DATA_HREF = re.compile(r'(?:data-href|data-url|data-link)=["\']([^"\']+)["\']')
_RE_LINK_TAG = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_RE_TAG_ATTR = re.compile(r'([a-zA-Z-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))')
_HTML_TYPES = ("text/html", "application/xhtml+xml")


def _drop_alternate_formats(html: str) -> str:
    """Remove <link rel="alternate"> tags whose type is not HTML (a page's
    JSON, Markdown or feed version), so their URLs are never queued.
    Alternates with no type or an HTML type (language versions) are kept."""
    def keep(m):
        attrs = {}
        for a in _RE_TAG_ATTR.finditer(m.group(0)):
            attrs[a.group(1).lower()] = a.group(2) or a.group(3) or a.group(4) or ""
        rels = attrs.get("rel", "").lower().split()
        ctype = attrs.get("type", "").strip().lower()
        if "alternate" in rels and ctype and not ctype.startswith(_HTML_TYPES):
            return ""
        return m.group(0)
    return _RE_LINK_TAG.sub(keep, html)
_RE_UI_ELEMENT_ID = re.compile(r'^(mintoc-|rh-|dropspot|#[a-z0-9]{8,})')
_RE_RSS_FEED = re.compile(r'/(?:rss|feed|atom)(?:\.xml)?/?$', re.IGNORECASE)
from ..processing.dates import get_best_date, url_has_old_date
from ..processing.classifier import classify_page, PageType
from ..processing.discovery import run_discovery, DiscoveryMap, load_robots_policy, parse_robots_sitemaps
from ..processing.robots import HostRateLimiter, RobotsPolicy
from ..processing.targeting import (
    SAMPLE_SIZE, Targeting, is_active as targeting_active, start_paths_seed_only,
    rel_next_paths as targeting_rel_next,
)
from ..convert.recipe import find_recipes
from ..processing.navigation import extract_navigation
from ..processing.outline import build_site_outline, SiteOutline
from ..convert.html_prep import safe_urljoin
from ..output.naming import FileNames, assignments_from_disk, fit_path
from ..output.writer import url_to_filepath, write_file
from ..output.broken_links import BrokenLinkTracker
from ..output.cache import CrawlCache
from ..output.run_summary import RunStats, build_summary, write_summary

# Discovery (sitemaps, feeds) spaces its requests at least this far apart,
# or by robots.txt Crawl-delay when that is longer.
DISCOVERY_MIN_INTERVAL = 1.0


async def run_crawl_mode(config: ScrapeConfig) -> dict:
    """Run the crawl and return the run summary dict (also written to disk)."""
    stats = RunStats()
    # Targeted crawling (--include, --exclude, --max-depth, --urls-file,
    # --follow-next); None when none is given, so nothing below changes
    targeting = None
    if targeting_active(config):
        targeting = Targeting(config, lambda link, path: _normalize_link_to_path(link, path, config))
    seed_only = start_paths_seed_only(config)
    query_starts: set[str] = set()      # start URLs with a query: fetched for links only
    not_recipe: list[str] = []          # --only-recipes: pages fetched but not written
    stopped_by_limit = False
    # Any targeting option keeps a start URL's query; without one the crawl
    # treats the start URL as it always has
    keep_query = bool(targeting is not None or config.only_recipes or config.max_pages
                      or config.site_name)
    # Replace any summary from an earlier run straight away, so a reader never
    # mistakes it for this run's. It is rewritten when the crawl ends.
    _warn_shared_site_name(config)
    _write_summary_quietly(config, build_summary(config, stats, in_progress=True))
    visited: set[str] = set()
    to_visit: deque[str] = deque()
    downloaded_assets: set[str] = set()
    skipped_out_of_scope: set[str] = set()
    broken_links = BrokenLinkTracker()
    unchanged_count = 0

    # Inline markdown converter
    md_converter = None
    if config.markdown:
        from pathlib import Path as _Path
        from ..convert.to_markdown import HTMLToMarkdown
        md_converter = HTMLToMarkdown(
            config.output_dir,
            output_dir=config.markdown_dir,
            base_url=config.base_url,
            domain=config.domain,
            absolute_links=config.markdown_only,
        )
        md_converter.output_dir.mkdir(parents=True, exist_ok=True)
        # Duplicates are decided by the crawl, against files final for the run
        md_converter.dedupe = False

    # Incremental cache
    cache = None
    if config.incremental:
        cache = CrawlCache(config.output_dir)
        cache.load()
        if not cache.has_assignments():
            # A cache that holds no file assignments: convert every page
            # again once, so none is left "unchanged" without a file
            cache.clear_pages()

    # Date filtering state
    discovery_map: DiscoveryMap = {}
    sections_kept = 0
    articles_kept = 0
    articles_skipped_old = 0
    skipped_precrawl = 0

    # Section batch state: ordered article URLs from section pages.
    # Each batch is (section_url, deque[article_url_paths in DOM order]).
    # When an old article is found in a batch, the rest is discarded
    # (unless --min-articles hasn't been reached for that section).
    section_batches: deque[tuple[str, deque[str]]] = deque()
    current_section_batch: tuple[str, deque[str]] | None = None
    current_batch_section: str = ""  # Section URL for current batch article

    # Per-section article counts for --min-articles enforcement.
    # Maps section_url -> number of articles kept from that section.
    section_article_counts: dict[str, int] = {}

    # Prefetch buffer: overlap network I/O for section batch articles.
    # Maps url_path -> asyncio.Task of the fetch coroutine.
    prefetch_tasks: dict[str, asyncio.Task] = {}

    # Article-to-section tracking for directory placement in MarkdownSites
    article_section_map: dict[str, str] = {}  # article_path -> section_path

    # Collected article metadata per section (for index.md generation)
    # section_path -> list of article metadata dicts
    section_articles: dict[str, list[dict]] = {}

    # Section page thumbnails: section_path -> {article_path: img_url}
    section_thumbnails: dict[str, dict[str, str]] = {}

    # --- Phase 0: Site Reconnaissance ---
    outline: SiteOutline | None = None
    robots_sitemaps: list[str] = []
    homepage_html: str | None = None
    feed_urls: list[str] = []

    # 1. Fetch robots.txt: access rules, Crawl-delay and extra sitemaps
    print("Running site reconnaissance...")
    # Fetched in a worker thread so Ctrl+C is not held up by a slow server
    try:
        robots, robots_content = await run_blocking(load_robots_policy, config)
    except asyncio.CancelledError:
        _note_interrupt(stats)
        robots, robots_content = RobotsPolicy.blocked("interrupted before robots.txt was read",
                                                      base_url=config.base_url), None
    if robots_content:
        robots_sitemaps = parse_robots_sitemaps(robots_content)
        if config.verbose and robots_sitemaps:
            print(f"  Found {len(robots_sitemaps)} sitemaps in robots.txt")
    _print_robots_status(robots, config)
    page_delay = robots.min_delay(config.wait_time)
    discovery_delay = robots.min_delay(min(config.wait_time, DISCOVERY_MIN_INTERVAL))
    # One limiter for the whole run, so the gap after the last discovery
    # request also applies to the first page request.
    limiter = HostRateLimiter(page_delay)
    limiter.reserve(urlparse(config.base_url).netloc)  # the robots.txt request just made

    # 2. Run discovery (sitemaps + RSS), returns homepage HTML for reuse.
    # A targeted crawl seeded only from its start URLs needs none of it
    # unless a date filter uses the sitemap and feed dates.
    try:
        if not (seed_only and not config.has_date_filter):
            discovery_map, homepage_html, feed_urls = await run_discovery(
                config, extra_sitemaps=robots_sitemaps,
                robots=robots, limiter=limiter.with_interval(discovery_delay),
            )
    except asyncio.CancelledError:
        _note_interrupt(stats)
    if discovery_map:
        print(f"  Found {len(discovery_map)} dated URLs from sitemaps/feeds")
    elif config.has_date_filter:
        print("  No dated URLs found (will classify pages after fetching)")

    # 3. Parse homepage navigation
    nav_links = []
    if homepage_html:
        nav_links = extract_navigation(homepage_html, config.base_url)
        if config.verbose:
            print(f"  Found {len(nav_links)} navigation links")

    # 4. Discover section pages from all homepage links
    section_pages: list[str] = []
    if homepage_html:
        section_pages = discover_section_pages(homepage_html, config.base_url)
        if config.verbose:
            print(f"  Discovered {len(section_pages)} section pages from homepage links")

    # 5. Build and save outline
    if nav_links or discovery_map:
        outline = build_site_outline(
            nav_links, discovery_map, feed_urls, robots_sitemaps, config,
        )
        outline.save(config.output_dir)
        print(f"  Site outline: {len(outline.nav_sections)} sections, {outline.sitemap_url_count} sitemap URLs")
        print(f"  Saved: site_outline.json")

    # Focused crawl: when max_articles + date filter + sections, only process
    # articles from section batches to prevent topic/author page explosion.
    nav_section_paths: set[str] = set()
    if outline:
        nav_section_paths = outline.all_section_paths()
    # Merge discovered section pages (broader than nav-only extraction)
    for p in section_pages:
        nav_section_paths.add(p)
    # Remove non-editorial paths (topic/tag/author/language variant pages)
    nav_section_paths = {p for p in nav_section_paths if not is_non_section_path(p)}
    focused_crawl = config.has_date_filter and config.max_articles > 0 and bool(nav_section_paths)
    if focused_crawl:
        print(f"  Focused crawl: {len(nav_section_paths)} section pages, max {config.max_articles} articles each")

    print()

    # Hash-based site detection
    visited_hashes: set[str] = set()
    hash_based_site = bool(config.starting_hash) or "#" in config.url

    # Seed the queue from outline or fallback
    if seed_only:
        # Only the start URLs, all at depth 0. A start URL with a query
        # (a site search) is fetched as given, for its links only.
        for start in (config.start_urls or [config.url]):
            parsed = urlparse(start)
            if parsed.query:
                query_starts.add(start)
                to_visit.append(start)
                targeting.add_start(start)
            else:
                path = posixpath.normpath(parsed.path or "/")
                path = path if path.startswith("/") else "/" + path
                to_visit.append(path)
                targeting.add_start(path)
    elif outline and outline.nav_sections and not hash_based_site:
        # Always include the start URL (fetched as given when it has a query
        # and a targeting option is set)
        _queue_start(config.url, to_visit, query_starts, keep_query)
        # Seed from navigation structure (recursively includes sub-sections)
        def _collect_section_paths(sections):
            for section in sections:
                to_visit.append(section.path)
                _collect_section_paths(section.sub_sections)
        _collect_section_paths(outline.nav_sections)
        # Also seed discovered section pages not already in nav
        seeded = outline.all_section_paths()
        for p in section_pages:
            if p not in seeded:
                to_visit.append(p)
        # Also add footer links (about, contact, etc.), skip in focused mode
        if not focused_crawl:
            for link in outline.footer_links:
                to_visit.append(link["path"])
        if config.verbose:
            print(f"Queue seeded from outline + discovered sections: {len(to_visit)} URLs")
    elif hash_based_site:
        to_visit.append(config.url)
    else:
        _queue_start(config.url, to_visit, query_starts, keep_query)
    if targeting is not None and not seed_only:
        # --exclude or --follow-next alone: the start URL is still always fetched
        if config.url in query_starts:
            targeting.add_start(config.url)
        else:
            start_path = posixpath.normpath(urlparse(config.url).path or "/")
            targeting.add_start(start_path if start_path.startswith("/") else "/" + start_path)
    if config.follow_next and not config.max_pages and config.max_depth is None:
        print("  Note: --follow-next without --max-pages or --max-depth follows pagination "
              "for as long as the site offers another page")

    # Concurrency semaphore for page fetching (reuses -c flag)
    page_sem = asyncio.Semaphore(config.concurrent)
    # Per-host pacing for every page fetch, prefetches included

    # Persistent HTTP client for asset downloads (connection reuse)
    # (none in markdown-only mode, which downloads no assets)
    asset_client = None
    if not config.markdown_only:
        asset_client = create_asset_client(config)
        await asset_client.__aenter__()

    # Each URL is written to its own files (see output/naming.py): its
    # natural paths, or a hash-suffixed name when another URL already has
    # one. Assignments from earlier runs come from the cache and never change.
    stored = _load_assignments(cache) if cache and cache.has_assignments() else None
    # Markdown files already on disk belong to the URL in their frontmatter
    # (pages from an earlier run, or entries written by feed mode): their
    # names are reserved for those URLs, added to any stored assignments
    on_disk = assignments_from_disk(config.markdown_dir) if config.markdown else {}
    if stored is None:
        stored = on_disk
    else:
        held = {a.get(k) for a in stored.values() for k in ("md", "html")}
        for disk_url, a in on_disk.items():
            if disk_url not in stored and a["md"] not in held:
                stored[disk_url] = a
    names = FileNames(stored)
    met: set[str] = set()                          # URLs written or unchanged this run
    run_hashes: dict[str, tuple[str, str]] = {}    # body hash -> (file, URL), final this run
    earlier_hashes: dict[str, tuple[str, str]] = {}  # the same from earlier runs, owner not met
    for _url, _a in names.by_url.items():
        if _a.get("hash") and _a.get("md"):
            earlier_hashes.setdefault(_a["hash"], (_a["md"], _url))
    deferred: dict[str, list[dict]] = {}           # owner URL -> pages with its earlier body

    def meet(url: str, body_hash: str | None) -> None:
        """url was written or found unchanged: its file is final for this run,
        so pages waiting on it can be decided."""
        met.add(url)
        for h, (_, owner) in list(earlier_hashes.items()):
            if owner == url:
                del earlier_hashes[h]
        a = names.by_url.get(url, {})
        if body_hash and a.get("md"):
            run_hashes.setdefault(body_hash, (a["md"], url))
        for waiting in deferred.pop(url, []):
            place_page(waiting, allow_defer=False)

    def place_page(entry: dict, allow_defer: bool = True) -> None:
        """Write a page, or record it as a duplicate of a file that is final
        for this run. A page whose body matches a file from an earlier run
        whose owner has not been met yet waits for that owner."""
        url = entry["url"]
        if entry["prepared"] is not None:
            hit = run_hashes.get(entry["hash"])
            if hit and hit[1] != url:
                write_page(entry, duplicate_of=hit[0])
                return
            early = earlier_hashes.get(entry["hash"])
            if allow_defer and early and early[1] != url and early[1] not in met:
                deferred.setdefault(early[1], []).append(entry)
                return
        write_page(entry, duplicate_of=None)

    def write_page(entry: dict, duplicate_of: str | None) -> None:
        url = entry["url"]
        prepared = entry["prepared"]
        natural = {
            "md": prepared.output_path if prepared is not None and not duplicate_of else None,
            "html": entry["filepath"] if entry["mirror_html"] is not None else None,
        }
        a = names.assign(url, natural)
        existed_before = entry["cached"] or bool(
            natural["html"] and os.path.exists(a["html"]))
        if natural["html"]:
            write_file(a["html"], entry["mirror_html"])
            print(f"  Saved: {a['html'].replace(config.output_dir + '/', '')}")
        md_written = None
        if natural["md"]:
            prepared.output_path = a["md"]
            md_converter.commit(prepared)
            md_written = a["md"]
            a["hash"] = entry["hash"]
            if config.markdown_only:
                existed_before = existed_before or md_converter.last_output_existed
        for kind in ("md", "html"):
            if natural[kind] and a[kind] != fit_path(natural[kind]):
                stats.record_renamed(url, a[kind], fit_path(natural[kind]))
                break
        if entry["error"]:
            # No Markdown was written: report the page as failed, and keep
            # it out of the cache so the next run converts it again
            stats.record_failed(url, f"conversion failed: {entry['error']}")
        elif duplicate_of:
            # Not cached: whether it is a duplicate is decided on every run
            stats.record_duplicate(url, duplicate_of)
            if cache:
                cache.remove_page(entry["url_path"])
            # Its body is now in another file: a Markdown file it had from
            # an earlier run would hold stale text, so it is removed (the
            # name stays reserved for this URL)
            own = names.by_url.get(url, {})
            if own.get("md") and own["md"] != duplicate_of and os.path.exists(own["md"]):
                os.remove(own["md"])
            own.pop("hash", None)
            return
        else:
            stats.record_saved(url, existed_before, md_written)
            if cache:
                cache.update_page(
                    entry["url_path"], entry["headers"], entry["html"], links=entry["links"],
                    published_date=entry["pub_date"], page_type=entry["page_type"],
                    next_links=_rel_next(entry["html"], entry["url_path"], config),
                )
        meet(url, entry["hash"] if md_written else None)

    if stats.interrupted:
        to_visit.clear()

    while to_visit or current_section_batch or section_batches:
        # Early exit: all nav section batches processed in focused mode
        if focused_crawl and not section_batches and not current_section_batch:
            if not any(s not in visited for s in nav_section_paths):
                remaining = len(to_visit)
                if remaining > 0 and config.verbose:
                    print(f"\n  All nav section batches processed, skipping {remaining} remaining URLs")
                to_visit.clear()
                break

        # --- Pick next URL: section batches have priority ---
        from_section_batch = False

        if current_section_batch:
            sec_url, sec_urls = current_section_batch
            if not sec_urls:
                current_section_batch = None
                continue
            url_or_path = sec_urls.popleft()
            from_section_batch = True
            current_batch_section = sec_url
            if not sec_urls:
                current_section_batch = None
        elif section_batches:
            current_section_batch = section_batches.popleft()
            continue  # Re-enter loop to pop from batch
        elif to_visit:
            url_or_path = to_visit.popleft()
        else:
            break

        # Skip UI element IDs
        if _RE_UI_ELEMENT_ID.match(url_or_path):
            continue

        url_path, hash_fragment, full_url = _parse_visit_item(
            url_or_path, config
        )

        visit_key = f"{url_path}{hash_fragment}" if hash_fragment else url_path
        discovery_only = url_or_path in query_starts
        if discovery_only:
            full_url, visit_key = url_or_path, url_or_path

        if visit_key in visited:
            continue
        if hash_fragment and hash_fragment in visited_hashes:
            continue

        if targeting is not None and not discovery_only:
            if targeting.is_start(visit_key):
                pass  # start URLs are always fetched
            else:
                reason = targeting.allow(url_path)
                if reason == "scope":
                    skipped_out_of_scope.add(url_path)
                    continue
                if reason:
                    targeting.record_skip(reason, url_path)
                    continue
        elif not discovery_only and not is_in_scope(url_path, config):
            skipped_out_of_scope.add(url_path)
            continue

        if not robots.can_fetch(full_url):
            if full_url not in stats.blocked_by_robots and config.verbose:
                print(f"  Skipped (robots.txt): {full_url}")
            stats.record_blocked(full_url)
            continue

        # Pre-crawl date filter: skip URLs known to be old before fetching.
        # Never skip the seed URL or non-article paths (sections must be fetched
        # so they can create article batches).
        # Exception: section batch articles respect --min-articles per section.
        is_seed = url_path in ("/", "/index.html", "/index.htm")
        if config.has_date_filter and not hash_fragment and not is_seed:
            # Only pre-filter URLs that look like articles (slug-like paths).
            # Section/navigation paths must always be fetched so they can
            # discover and queue their article links.
            if has_article_slug(url_path):
                sec_count = section_article_counts.get(current_batch_section, 0) if from_section_batch else 0
                need_more_from_batch = (
                    from_section_batch
                    and config.min_articles > 0
                    and sec_count < config.min_articles
                )
                if not need_more_from_batch:
                    disc_date = discovery_map.get(url_path)
                    if disc_date and disc_date < config.cutoff_date:
                        skipped_precrawl += 1
                        if config.verbose:
                            print(f"  Skipped (pre-crawl: sitemap date): {full_url}")
                        if from_section_batch:
                            skipped_precrawl += _exhaust_section_batch(
                                current_section_batch, prefetch_tasks, config.verbose,
                            )
                            current_section_batch = None
                        continue
                    if url_has_old_date(url_path, config.cutoff_date):
                        skipped_precrawl += 1
                        if config.verbose:
                            print(f"  Skipped (pre-crawl: URL date): {full_url}")
                        if from_section_batch:
                            skipped_precrawl += _exhaust_section_batch(
                                current_section_batch, prefetch_tasks, config.verbose,
                            )
                            current_section_batch = None
                        continue

        if config.max_pages and stats.fetched >= config.max_pages:
            stopped_by_limit = True
            print(f"  Stopped: --max-pages {config.max_pages} reached")
            break

        visited.add(visit_key)
        if hash_fragment:
            visited_hashes.add(hash_fragment)

        try:
            print(f"Fetching page: {full_url}")

            # Check prefetch buffer first (concurrent fetching)
            from_prefetch = url_path in prefetch_tasks
            if from_prefetch:
                try:
                    html, status, page_info = await prefetch_tasks.pop(url_path)
                except asyncio.CancelledError:
                    if _this_task_cancelled():
                        raise  # Ctrl+C, not a cancelled prefetch
                    html, status, page_info = "", 0, {"error": "Prefetch cancelled"}
                except Exception as e:
                    html, status, page_info = "", 0, {"error": redact(f"{type(e).__name__}: {e}", config.proxy)}
            elif config.needs_browser:
                await limiter.wait(full_url)
                html, status, page_info = await _fetch_with_browser(full_url, config)
            else:
                # Build conditional headers for incremental mode
                cond_headers = None
                if cache:
                    cond_headers = cache.get_page_headers(url_path)

                html, status, page_info = await _fetch_with_http_async(
                    full_url, config, page_sem, headers=cond_headers, limiter=limiter,
                )

            # Counted once the request has completed (response or network error)
            stats.fetched += 1

            if not config.needs_browser:
                # Handle 304 Not Modified
                if status == 304:
                    print(f"  Unchanged (304): {full_url}")
                    unchanged_count += 1
                    _meet_unchanged(full_url, names, stats, meet)
                    cached_links = cache.get_page_links(url_path)
                    if cached_links and targeting is not None:
                        # rel="next" comes from the cache, as the page is not re-read
                        targeting.note_links(cached_links, None, visit_key, url_path,
                                             next_paths=cache.get_page_next(url_path))
                    if cached_links:
                        _queue_links(
                            cached_links, url_path, config, visited, visited_hashes,
                            to_visit, hash_based_site,
                            discovery_map=discovery_map,
                            cutoff=config.cutoff_date,
                        )
                    continue

            # Check for fetch failure (status 0 = local error, not a server response)
            if status == 0:
                error_msg = page_info.get("error", "Unknown error")
                print(f"  Fetch failed ({error_msg}): {full_url}")
                broken_links.add(full_url)
                stats.record_failed(full_url, error_msg)
                continue

            # Check HTTP status
            if status >= 400:
                print(f"  Skipped (HTTP {status}): {full_url}")
                broken_links.add(full_url)
                stats.record_failed(full_url, f"HTTP {status}")
                continue

            # Only HTML is converted: a page's raw Markdown, JSON or XML
            # version would be written as a second, garbled copy of the page
            content_type = _content_type(page_info)
            if content_type and not content_type.startswith(_HTML_TYPES):
                print(f"  Skipped (not HTML, {content_type}): {full_url}")
                stats.record_non_html(full_url, content_type)
                continue

            # Quick error page check
            if is_error_page_html(html):
                print(f"  Skipped (error page): {full_url}")
                broken_links.add(full_url)
                stats.record_failed(full_url, "error page")
                continue

            # Cloudflare challenge
            if is_cloudflare_challenge(html):
                print("  Cloudflare challenge detected, waiting...")
                await asyncio.sleep(5)

            # JS content wait. The politeness delay between requests is
            # applied by the per-host limiter before each fetch.
            if config.js_render and page_info.get("page"):
                await wait_for_js_content(page_info["page"], config.wait_time)
                html = await page_info["page"].content()

            # Detailed error page check on rendered content
            if _check_rendered_error_page(html):
                print(f"  Skipped (error page): {full_url}")
                broken_links.add(full_url)
                stats.record_failed(full_url, "error page")
                continue

            # A start URL with a query (a site search) is read for its links
            # only: its query cannot reach a file name or the cache
            if discovery_only:
                links = _extract_links_from_html(html)
                if targeting is not None:
                    targeting.note_links(links, html, visit_key, url_path)
                _queue_links(
                    links, url_path, config, visited, visited_hashes,
                    to_visit, hash_based_site,
                    discovery_map=discovery_map, cutoff=config.cutoff_date,
                )
                continue

            # Content-hash check: skip if content hasn't changed
            if cache and cache.is_page_unchanged(url_path, html):
                print(f"  Unchanged (same content): {full_url}")
                unchanged_count += 1
                _meet_unchanged(full_url, names, stats, meet)
                links = _extract_links_from_html(html)
                if targeting is not None:
                    targeting.note_links(links, html, visit_key, url_path)
                _queue_links(
                    links, url_path, config, visited, visited_hashes,
                    to_visit, hash_based_site,
                    discovery_map=discovery_map,
                    cutoff=config.cutoff_date,
                )
                continue

            # Extract links BEFORE rewriting
            if config.needs_browser and page_info.get("page"):
                links = await extract_links_browser(page_info["page"], config.base_url)
            else:
                links = _extract_links_from_html(html)
            if targeting is not None:
                targeting.note_links(links, html, visit_key, url_path)

            # --- Date filtering: classify page and handle sections/articles ---
            page_type_str = None
            pub_date_str = None
            date_result = None
            is_min_article = False  # Old article kept only for min-articles quota

            if config.has_date_filter:
                page_type = classify_page(url_path, html)
                # Override: known nav section paths are always sections
                if page_type != PageType.SECTION and url_path.rstrip("/") in nav_section_paths:
                    page_type = PageType.SECTION
                page_type_str = page_type.value
                resp_headers = page_info.get("headers", {})
                date_result = get_best_date(url_path, html, resp_headers)

                if page_type == PageType.SECTION:
                    sections_kept += 1
                    if config.verbose:
                        print(f"  [section] keeping navigation page")

                    # The root page (/) is too broad for batching, every path
                    # is a "child" of /, so it would monopolise all articles
                    # and prevent real section pages from getting their own
                    # min-articles quotas.  Let the homepage queue normally.
                    is_root = url_path in ("/", "/index.html", "/index.htm")
                    # In focused mode, only nav section pages create article batches
                    is_batchable = not focused_crawl or url_path.rstrip("/") in nav_section_paths

                    if not is_root and is_batchable:
                        # Split links: article links become an ordered batch,
                        # section/nav links go to the regular queue.
                        article_links, other_links = _split_section_links(
                            links, url_path, config, visited,
                        )
                        if article_links:
                            if config.max_articles > 0 and len(article_links) > config.max_articles:
                                if config.verbose:
                                    print(f"  [section] capped {len(article_links)} articles to {config.max_articles}")
                                article_links = article_links[:config.max_articles]
                            section_batches.append((url_path, deque(article_links)))
                            # Record which section owns these articles
                            for ap in article_links:
                                article_section_map[ap] = url_path
                            # Extract thumbnails from section page HTML
                            section_thumbnails[url_path] = _extract_article_thumbnails(html, article_links)
                            # Initialize article metadata list
                            if url_path not in section_articles:
                                section_articles[url_path] = []
                            if config.verbose:
                                print(f"  [section] queued {len(article_links)} article links for ordered processing")
                            # Start prefetching first batch of articles immediately
                            if not config.needs_browser and not config.max_pages:
                                for p in article_links[:config.concurrent]:
                                    if (p not in prefetch_tasks and p not in visited
                                            and robots.can_fetch(p)):
                                        pf_url = config.base_url + p
                                        pf_headers = cache.get_page_headers(p) if cache else None
                                        prefetch_tasks[p] = asyncio.create_task(
                                            _fetch_with_http_async(
                                                pf_url, config, page_sem, headers=pf_headers,
                                                limiter=limiter,
                                            )
                                        )
                        # Only queue non-article links below (articles are in the batch)
                        links = other_links
                    elif is_root:
                        # Root page with date filtering: only queue section/nav
                        # links.  Articles will be discovered via section pages.
                        links = [l for l in links if not has_article_slug(
                            _normalize_link_to_path(l, url_path, config) or ""
                        )]
                        if config.verbose:
                            print(f"  [section] root page: queuing section links only (filtered articles)")

                elif page_type == PageType.ARTICLE or page_type == PageType.UNKNOWN:
                    if date_result:
                        pub_date_str = date_result.date.isoformat()
                        if date_result.date < config.cutoff_date:
                            # Check min_articles per section: keep old articles
                            # from section batches until the section minimum is reached
                            sec_count = section_article_counts.get(current_batch_section, 0) if from_section_batch else 0
                            need_more = (
                                from_section_batch
                                and config.min_articles > 0
                                and sec_count < config.min_articles
                            )
                            if need_more:
                                articles_kept += 1
                                is_min_article = True
                                section_article_counts[current_batch_section] = sec_count + 1
                                if config.verbose:
                                    print(f"  [article] keeping (min-articles {sec_count + 1}/{config.min_articles} for section): published {date_result.raw}")
                            else:
                                articles_skipped_old += 1
                                if config.verbose:
                                    print(f"  [article] skipped: published {date_result.raw} (before cutoff)")

                                # Bail out of section batch, articles are newest-first
                                if from_section_batch:
                                    skipped_precrawl += _exhaust_section_batch(
                                        current_section_batch, prefetch_tasks, config.verbose,
                                    )
                                    current_section_batch = None

                                # Don't queue links from old articles
                                continue
                        else:
                            articles_kept += 1
                            if from_section_batch:
                                sec_kept = section_article_counts.get(current_batch_section, 0) + 1
                                section_article_counts[current_batch_section] = sec_kept
                                if config.max_articles > 0 and sec_kept >= config.max_articles:
                                    _exhaust_section_batch(
                                        current_section_batch, prefetch_tasks, config.verbose, "section max",
                                    )
                                    current_section_batch = None
                            if config.verbose:
                                print(f"  [article] keeping: published {date_result.raw}")
                    else:
                        # No date found, keep conservatively
                        articles_kept += 1
                        if from_section_batch:
                            sec_kept = section_article_counts.get(current_batch_section, 0) + 1
                            section_article_counts[current_batch_section] = sec_kept
                            if config.max_articles > 0 and sec_kept >= config.max_articles:
                                _exhaust_section_batch(
                                    current_section_batch, prefetch_tasks, config.verbose, "section max",
                                )
                                current_section_batch = None
                        if config.verbose:
                            print(f"  [{page_type.value}] keeping: no date found (conservative)")

            # --only-recipes: a page without schema.org Recipe data is read for
            # its links but not written
            if config.only_recipes and not find_recipes(html):
                print(f"  Not a recipe (links followed, not written): {full_url}")
                not_recipe.append(full_url)
                if not is_min_article and not focused_crawl:
                    _queue_links(
                        links, url_path, config, visited, visited_hashes,
                        to_visit, hash_based_site,
                        discovery_map=discovery_map, cutoff=config.cutoff_date,
                    )
                continue

            # Download assets (skip entirely when --no-markdown + --no-include-assets;
            # skip for min-articles filler; section pages download images only
            # for thumbnails in index.md)
            if config.markdown_only:
                pass  # Markdown only: images stay as absolute links, nothing downloaded
            elif not config.markdown and not config.include_assets:
                pass  # No assets needed, raw HTML cache only
            elif config.has_date_filter and is_min_article:
                pass  # Skip assets for min-articles filler
            elif config.has_date_filter and page_type_str == "section":
                # Section pages: download only images (for thumbnails in index.md)
                await discover_and_download_assets(
                    html, config, downloaded_assets, cache=cache,
                    http_client=asset_client, images_only=True,
                )
            else:
                await discover_and_download_assets(
                    html, config, downloaded_assets, cache=cache,
                    http_client=asset_client,
                )

            # Save the page. It counts as changed when an earlier run saved it.
            # In markdown-only mode the HTML path only names the page; nothing
            # is written there and the converter is handed the HTML directly.
            # Nothing is written until the page's files are assigned.
            filepath = os.path.abspath(url_to_filepath(url_path, config, hash_fragment))
            mirror_html = None
            if not config.markdown_only:
                # Rewrite URLs for offline viewing
                mirror_html = rewrite_urls(html, url_path, config)
            html_for_md = html if config.markdown_only else mirror_html
            prepared = None
            # Inline markdown conversion
            if md_converter:
                md_converter.last_error = None
                md_meta = {"url": full_url, "domain": config.domain}
                if config.has_date_filter:
                    # Metadata already extracted during date filtering
                    if page_type_str:
                        md_meta["page_type"] = page_type_str
                    if pub_date_str:
                        md_meta["date"] = pub_date_str
                    if date_result:
                        md_meta["date_source"] = date_result.source.name
                else:
                    # No date filter, extract metadata now
                    resp_headers = page_info.get("headers", {})
                    date_result_now = get_best_date(url_path, html, resp_headers)
                    if date_result_now:
                        md_meta["date"] = date_result_now.date.isoformat()
                        md_meta["date_source"] = date_result_now.source.name
                    page_type_now = classify_page(url_path, html)
                    # Override: known nav section paths are always sections
                    if page_type_now != PageType.SECTION and url_path.rstrip("/") in nav_section_paths:
                        page_type_now = PageType.SECTION
                    md_meta["page_type"] = page_type_now.value

                # Genuine section = classified as section AND not a topic/tag/author page
                is_section_page = (
                    md_meta.get("page_type") == "section"
                    and not is_non_section_path(url_path)
                )

                # Section batch articles get placed in section directory
                if from_section_batch and current_batch_section:
                    if is_section_page:
                        # Section page discovered from a batch, still _index.md
                        from ..output.writer import section_index_filepath
                        md_output_path = section_index_filepath(url_path, config)
                    else:
                        from ..output.writer import article_filepath_in_section
                        md_output_path = article_filepath_in_section(
                            url_path, current_batch_section, config,
                        )
                    prepared = md_converter.prepare(
                        _Path(filepath), metadata=md_meta,
                        output_path=md_output_path, html=html_for_md,
                    )
                elif is_section_page:
                    # Section pages become _index.md in their own directory
                    from ..output.writer import section_index_filepath
                    md_output_path = section_index_filepath(url_path, config)
                    prepared = md_converter.prepare(
                        _Path(filepath), metadata=md_meta,
                        output_path=md_output_path, html=html_for_md,
                    )
                else:
                    prepared = md_converter.prepare(
                        _Path(filepath), metadata=md_meta, html=html_for_md,
                    )


                # Track article metadata for section index generation
                # (skip section pages, they are sub-sections, not articles)
                if from_section_batch and current_batch_section and not is_section_page:
                    if current_batch_section not in section_articles:
                        section_articles[current_batch_section] = []
                    from ..output.writer import article_relpath_in_section, extract_slug
                    article_title = (
                        _extract_title_from_html(html)
                        or extract_slug(url_path).replace("-", " ").title()
                    )
                    article_desc = _extract_description_from_html(html)
                    article_relpath = article_relpath_in_section(url_path, current_batch_section)
                    thumb_url = section_thumbnails.get(current_batch_section, {}).get(url_path)
                    if config.markdown_only:
                        # No local images: link the live image
                        thumb_filename = safe_urljoin(full_url, thumb_url) if thumb_url else None
                    else:
                        thumb_filename = (
                            thumb_url.split("?")[0].split("#")[0].rsplit("/", 1)[-1]
                            if thumb_url else None
                        )
                    section_articles[current_batch_section].append({
                        "title": article_title,
                        "filename": article_relpath + ".md",
                        "date": md_meta.get("date"),
                        "word_count": None,  # Not available until after conversion
                        "description": article_desc,
                        "thumbnail": thumb_filename,
                    })

            entry = {
                "url": full_url, "url_path": url_path, "filepath": filepath,
                "mirror_html": mirror_html, "prepared": prepared,
                "cached": cache is not None and cache.has_page(url_path),
                "error": md_converter.last_error if md_converter else None,
                "hash": prepared.content_hash if prepared else None,
                "headers": page_info.get("headers", {}), "html": html, "links": links,
                "pub_date": pub_date_str, "page_type": page_type_str,
            }
            place_page(entry)

            # Queue extracted links (for sections, only non-article links reach here).
            # Old articles kept via min-articles don't queue links, they're filler
            # content, not sources for further discovery.
            if not is_min_article and not focused_crawl:
                _queue_links(
                    links, url_path, config, visited, visited_hashes,
                    to_visit, hash_based_site,
                    discovery_map=discovery_map,
                    cutoff=config.cutoff_date,
                )

            # Kick off prefetches for upcoming URLs (none with --max-pages,
            # which caps requests exactly)
            if not config.needs_browser and not config.max_pages:
                # Section batch articles
                if current_section_batch:
                    _, upcoming = current_section_batch
                    for p in itertools.islice(upcoming, config.concurrent):
                        if (p not in prefetch_tasks and p not in visited
                                and robots.can_fetch(p)):
                            pf_url = config.base_url + p
                            pf_headers = cache.get_page_headers(p) if cache else None
                            prefetch_tasks[p] = asyncio.create_task(
                                _fetch_with_http_async(
                                    pf_url, config, page_sem, headers=pf_headers,
                                    limiter=limiter,
                                )
                            )
                # Regular queue: prefetch upcoming items for parallelism
                if config.has_date_filter and to_visit:
                    for p in itertools.islice(to_visit, config.concurrent):
                        if p.startswith(("http://", "https://", "#")):
                            continue
                        if (p not in prefetch_tasks and p not in visited
                                and robots.can_fetch(p)):
                            pf_url = config.base_url + p
                            pf_headers = cache.get_page_headers(p) if cache else None
                            prefetch_tasks[p] = asyncio.create_task(
                                _fetch_with_http_async(
                                    pf_url, config, page_sem, headers=pf_headers,
                                    limiter=limiter,
                                )
                            )

        except (KeyboardInterrupt, asyncio.CancelledError):
            # Ctrl+C under asyncio.run() arrives as a cancelled task
            _note_interrupt(stats)
            break
        except Exception as e:
            error_text = redact(f"{type(e).__name__}: {e}", config.proxy)
            print(f"Error on {full_url}: {error_text}")
            stats.record_failed(full_url, error_text)
            if config.verbose:
                import traceback
                traceback.print_exc()

    # Cancel any remaining prefetch tasks
    for task in prefetch_tasks.values():
        task.cancel()
    prefetch_tasks.clear()

    # Pages whose body matched a file from an earlier run whose owner was
    # never met: that file still holds the body, so they are duplicates of
    # it (or get their own file when it has gone). An interrupted run
    # settles nothing.
    if not stats.interrupted:
        for owner in sorted(deferred):
            a = names.by_url.get(owner, {})
            for entry in deferred.pop(owner):
                if a.get("hash") == entry["hash"] and a.get("md") and os.path.exists(a["md"]):
                    write_page(entry, duplicate_of=a["md"])
                else:
                    place_page(entry, allow_defer=False)
    deferred.clear()

    # Generate section _index.md listing pages (skipped with --no-index-pages)
    md_output = config.markdown_dir
    if md_converter and section_articles and config.index_pages:
        from ..convert.to_markdown import generate_section_index
        import os as _os
        for sec_path, articles in section_articles.items():
            if not articles:
                continue
            # Skip non-editorial paths (topic/tag/author/language pages)
            if is_non_section_path(sec_path):
                continue
            section_label = sec_path.rsplit("/", 1)[-1].replace("-", " ").title() or "Home"
            section_sub_sections = []
            if outline:
                sec_obj = _find_section_in_outline(outline, sec_path)
                if sec_obj:
                    section_label = sec_obj.label
                    sec_prefix = sec_path.strip("/")
                    section_sub_sections = []
                    for sub in sec_obj.sub_sections:
                        sub_stripped = sub.path.strip("/")
                        if sec_prefix and sub_stripped.startswith(sec_prefix + "/"):
                            dir_name = sub_stripped[len(sec_prefix) + 1:]
                        else:
                            dir_name = sub_stripped.rsplit("/", 1)[-1]
                        section_sub_sections.append(
                            {"label": sub.label, "dir_name": dir_name}
                        )
            index_md = generate_section_index(
                section_label=section_label,
                section_url=config.base_url + sec_path,
                domain=config.domain,
                articles=articles,
                sub_sections=section_sub_sections,
            )
            section_dir = sec_path.strip("/")
            index_path = (
                _os.path.join(md_output, section_dir, "_index.md")
                if section_dir
                else _os.path.join(md_output, "_index.md")
            )
            _os.makedirs(_os.path.dirname(index_path), exist_ok=True)
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(index_md)
            if config.verbose:
                print(f"  Generated section index: {section_dir or 'root'}/_index.md ({len(articles)} articles)")

    # Copy referenced images to each section's images/ directory
    if md_converter and section_articles and not config.markdown_only:
        source_img_dir = os.path.join(config.output_dir, "images")
        if os.path.isdir(source_img_dir):
            _copy_section_images(
                md_output, section_articles, source_img_dir, config.verbose,
            )

    # Save cache first, so an interrupted run still resumes from here
    if cache:
        _save_assignments(cache, names)
        cache.save()

    # Close persistent HTTP client
    try:
        if asset_client is not None:
            await asset_client.aclose()
    except asyncio.CancelledError:
        _note_interrupt(stats)

    # Summary
    print("\n--- Scraping Interrupted ---" if stats.interrupted else "\n--- Scraping Complete ---")
    saved_count = len(visited) - broken_links.count - unchanged_count - articles_skipped_old
    print(f"Pages saved: {saved_count}")
    if unchanged_count > 0:
        print(f"Pages unchanged (skipped): {unchanged_count}")
    print(f"Assets downloaded: {len(downloaded_assets)}")
    if md_converter:
        asset_count = 0 if config.markdown_only else md_converter.copy_assets()
        print(f"Markdown converted: {md_converter.converted}")
        if asset_count:
            print(f"Markdown assets copied: {asset_count}")

    if outline:
        print(f"Site outline: {len(outline.nav_sections)} nav sections, {outline.sitemap_url_count} sitemap URLs")

    if focused_crawl:
        print(f"Focused crawl: {len(nav_section_paths)} nav sections targeted")

    if config.has_date_filter:
        print(f"Sections kept: {sections_kept}")
        print(f"Articles kept (recent): {articles_kept}")
        if articles_skipped_old > 0:
            print(f"Articles skipped (too old): {articles_skipped_old}")
        if skipped_precrawl > 0:
            print(f"URLs skipped (pre-crawl/section cutoff): {skipped_precrawl}")
        if config.min_articles and section_article_counts:
            min_kept = sum(min(v, config.min_articles) for v in section_article_counts.values())
            print(f"Min-articles kept (old): {min_kept} across {len(section_article_counts)} sections")

    if broken_links.count > 0:
        print(f"Skipped (error pages): {broken_links.count}")
        broken_links.write_report(config.output_dir, config.url)

    if config.stay_in_path and skipped_out_of_scope:
        print(f"Skipped (out of scope): {len(skipped_out_of_scope)}")

    if stats.blocked_by_robots:
        print(f"Skipped (blocked by robots.txt): {len(stats.blocked_by_robots)}")

    print(f"Output folder: {config.output_dir}")

    stats.skipped_old = articles_skipped_old
    if (targeting is not None or config.only_recipes or config.max_pages
            or config.site_name):
        stats.targeting = {
            **(targeting.summary(fetched=visited) if targeting is not None
               else {"skipped_by_include": 0, "skipped_by_exclude": 0, "skipped_by_depth": 0}),
            "skipped_not_recipe": len(not_recipe),
            "not_recipe_sample": not_recipe[:SAMPLE_SIZE],
            "stopped_by_limit": stopped_by_limit,
        }
    summary = build_summary(config, stats)
    summary_path = _write_summary_quietly(config, summary)
    if summary_path:
        print(f"Run summary: {summary_path}")
    return summary


def _this_task_cancelled() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


def _note_interrupt(stats: RunStats) -> None:
    """Record Ctrl+C or SIGTERM and let the crawl finish its bookkeeping (cache, summary)."""
    if not stats.interrupted:
        stats.interrupted_by = interrupts.signal_name(interrupts.received())
        print(f"\nCrawl interrupted ({stats.interrupted_by}). Saving progress...")
    stats.interrupted = True
    task = asyncio.current_task()
    if task is not None and hasattr(task, "uncancel"):
        task.uncancel()


def _write_summary_quietly(config: ScrapeConfig, summary: dict) -> str | None:
    try:
        return write_summary(config, summary)
    except OSError as e:
        print(f"Warning: could not write run summary ({e})")
        return None


def _print_robots_status(robots: RobotsPolicy, config: ScrapeConfig) -> None:
    """One line on how robots.txt shapes this run."""
    if not robots.enforced:
        return  # The CLI already printed the --ignore-robots warning
    if robots.disallow_all:
        print(f"  {robots.note}. Nothing will be crawled "
              "(use --ignore-robots only if you have permission).")
        return
    if not robots.has_rules:
        print("  robots.txt: none found, all URLs allowed")
        return
    line = "  robots.txt: rules applied"
    if robots.crawl_delay is not None:
        line += f", Crawl-delay {robots.crawl_delay:g}s"
    print(line)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _exhaust_section_batch(
    batch: tuple[str, deque[str]] | None,
    prefetch_tasks: dict[str, asyncio.Task],
    verbose: bool,
    label: str = "section cutoff",
) -> int:
    """Cancel remaining section batch items and their prefetch tasks.

    Returns the count of cancelled items (for skipped_precrawl tracking).
    """
    if not batch:
        return 0
    remaining = batch[1]
    count = len(remaining)
    if verbose and remaining:
        print(f"  [{label}] skipping {count} remaining articles from section")
    for p in remaining:
        task = prefetch_tasks.pop(p, None)
        if task:
            task.cancel()
    remaining.clear()
    return count


def _warn_shared_site_name(config: ScrapeConfig) -> None:
    """--site-name folders are meant for one site: warn when the folder's last
    run crawled another host, since its files and cache would then be
    rewritten on every switch."""
    if not config.site_name:
        return
    try:
        with open(os.path.join(config.output_dir, "run_summary.json"), encoding="utf-8") as f:
            previous = json.load(f).get("start_url") or ""
    except (OSError, ValueError, AttributeError):
        return
    old_host = urlparse(previous).netloc.lower()
    if old_host and old_host != config.domain.lower():
        print(f"  Warning: --site-name {config.site_name} last held a crawl of another host; "
              "use one --site-name per site, or each run rewrites the other's files")


def _queue_start(url: str, to_visit, query_starts: set[str], keep_query: bool) -> None:
    """Queue the start URL. With a targeting option, one with a query (a site
    search) is queued as given and fetched for its links only; otherwise its
    path is queued (the query is dropped, as in every untargeted crawl)."""
    parsed = urlparse(url)
    if parsed.query and keep_query:
        query_starts.add(url)
        to_visit.append(url)
    else:
        to_visit.append(parsed.path or "/")


def _rel_next(html: str, url_path: str, config: ScrapeConfig) -> list[str]:
    """The page's rel="next" paths, kept in the cache with its links."""
    return targeting_rel_next(html, url_path, lambda link, path: _normalize_link_to_path(link, path, config))


def _parse_visit_item(url_or_path: str, config: ScrapeConfig) -> tuple[str, str, str]:
    """Parse a queue item into (url_path, hash_fragment, full_url)."""
    if url_or_path.startswith(("http://", "https://")):
        parsed = urlparse(url_or_path)
        path = posixpath.normpath(parsed.path)
        if not path.startswith("/"):
            path = "/" + path
        frag = f"#{parsed.fragment}" if parsed.fragment else ""
        return path, frag, config.base_url + path + frag
    elif url_or_path.startswith("#"):
        parsed = urlparse(config.url)
        url_path = parsed.path
        return url_path, url_or_path, config.base_url + url_path + url_or_path
    else:
        path = posixpath.normpath(url_or_path)
        if not path.startswith("/"):
            path = "/" + path
        return path, "", config.base_url + path


def _fetch_with_http(
    url: str, config: ScrapeConfig, headers: dict | None = None,
) -> tuple[str, int, dict]:
    """Fetch via HTTP (synchronous), return (html, status_code, info_dict).

    On fetch failure, returns status 0 with error info in the dict.
    """
    try:
        response = fetch_page_http(url, config, headers=headers)
        status = response.status
        if status == 304:
            return "", 304, {"headers": dict(response.headers)}
        html = response.body.decode(response.encoding or "utf-8")
        return html, status, {"headers": dict(response.headers)}
    except Exception as e:
        return "", 0, {"error": redact(f"{type(e).__name__}: {e}", config.proxy)}


async def _fetch_with_http_async(
    url: str, config: ScrapeConfig, sem: asyncio.Semaphore,
    headers: dict | None = None,
    limiter: HostRateLimiter | None = None,
) -> tuple[str, int, dict]:
    """Fetch via HTTP with semaphore for concurrency control.

    Scrapling's Fetcher.get() is synchronous, so we run it in a thread
    to allow concurrent fetching of multiple pages. The limiter spaces
    request starts per host, so prefetch bursts keep the politeness delay.
    """
    async with sem:
        if limiter is not None:
            await limiter.wait(url)
        # A daemon thread, so an abandoned request cannot delay exit after Ctrl+C
        return await run_blocking(_fetch_with_http, url, config, headers)


async def _fetch_with_browser(url: str, config: ScrapeConfig) -> tuple[str, int, dict]:
    """Fetch via browser, return (html, status_code, info_dict).

    On fetch failure, returns status 0 with error info in the dict.
    """
    try:
        response = await fetch_page_browser(url, config, wait_time=1.0)
        status = response.status
        html = response.body.decode(response.encoding or "utf-8")
        return html, status, {}
    except Exception as e:
        return "", 0, {"error": redact(f"{type(e).__name__}: {e}", config.proxy)}


def _check_rendered_error_page(html: str) -> bool:
    """Check rendered HTML for error page patterns using BeautifulSoup."""
    try:
        soup = BeautifulSoup(html[:5000], "lxml")
        title = soup.find("title")
        title_text = title.get_text() if title else ""
        h1 = soup.find("h1")
        h1_text = h1.get_text() if h1 else ""
        body = soup.find("body")
        body_text = body.get_text()[:2000] if body else ""
        return is_error_page_content(title_text, h1_text, body_text)
    except Exception:
        return False


def _load_assignments(cache) -> dict[str, dict]:
    """URL -> assigned files from earlier runs, with absolute paths."""
    if not cache:
        return {}
    base = os.path.dirname(os.path.abspath(cache.cache_path))
    out = {}
    for url, a in cache.get_assignments().items():
        out[url] = {
            k: (os.path.normpath(os.path.join(base, v)) if k != "hash" and v else v)
            for k, v in a.items()
        }
    return out


def _save_assignments(cache, names) -> None:
    """Store assignments with paths relative to the cache's folder, so the
    output folder can be moved."""
    base = os.path.dirname(os.path.abspath(cache.cache_path))
    cache.set_assignments({
        url: {k: (os.path.relpath(v, base) if k != "hash" and v else v) for k, v in a.items()}
        for url, a in sorted(names.by_url.items())
    })


def _meet_unchanged(url, names, stats, meet) -> None:
    """An unchanged page keeps its files."""
    renamed = names.renamed_entry(url)
    if renamed:
        stats.record_renamed(url, renamed[0], renamed[1])
    stats.unchanged.append(url)
    meet(url, names.by_url.get(url, {}).get("hash"))


def _content_type(page_info: dict) -> str:
    """The response's media type, lowercase, or "" when the server sent none."""
    for key, value in (page_info.get("headers") or {}).items():
        if str(key).lower() == "content-type":
            return str(value).split(";", 1)[0].strip().lower()
    return ""


def _extract_links_from_html(html: str) -> list[str]:
    """Extract links from raw HTML via regex (preserves DOM order)."""
    seen = set()
    links = []
    html = _drop_alternate_formats(html)
    for m in _RE_HREF.finditer(html):
        href = m.group(1)
        if href not in seen:
            seen.add(href)
            links.append(href)
    for m in _RE_DATA_HREF.finditer(html):
        href = m.group(1)
        if href not in seen:
            seen.add(href)
            links.append(href)
    return links


def _normalize_link_to_path(
    link: str, current_path: str, config: ScrapeConfig,
) -> str | None:
    """Normalize a raw link to an absolute URL path, or None if invalid/external."""
    if not link:
        return None
    if link.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
        return None
    # Protocol-relative URLs are external
    if link.startswith("//"):
        return None

    clean = link.split("?")[0].split("#")[0]
    if not clean:
        return None
    if SKIP_EXTENSIONS.search(clean):
        return None

    if clean.startswith(config.base_url):
        clean = clean[len(config.base_url):]
    elif clean.startswith(("http://", "https://")):
        return None  # External

    if not clean.startswith("/"):
        current_dir = current_path.rsplit("/", 1)[0] if "/" in current_path else "/"
        clean = posixpath.join(current_dir, clean)

    clean = posixpath.normpath(clean)
    if not clean.startswith("/"):
        clean = "/" + clean

    # Skip Cloudflare infrastructure paths (email protection, etc.)
    if clean.startswith("/cdn-cgi/"):
        return None

    return clean



def _looks_like_article_path(path: str, section_path: str) -> bool:
    """Check if a URL path looks like an article that's a child of the section.

    Article links are children of the section with a slug-like final segment
    (long, hyphenated, e.g., /news/garmin-catalyst-2-driving-coach).
    Navigation/section links have short, clean segments (e.g., /reviews, /news/gaming).
    """
    # Normalize section prefix for child-checking
    if section_path == "/":
        section_prefix = "/"
    else:
        section_prefix = section_path.rstrip("/") + "/"

    # Must be a child of the section
    if not path.startswith(section_prefix):
        return False

    # Get the part after the section prefix
    rel = path[len(section_prefix):]
    if not rel:
        return False

    return has_article_slug(path)


def _split_section_links(
    links: list[str],
    current_path: str,
    config: ScrapeConfig,
    visited: set[str],
) -> tuple[list[str], list[str]]:
    """Split a section page's links into article links (DOM order) and other links.

    Article links = pages with slug-like URLs (likely articles listed by this section).
    The article doesn't need to be a child path of the section, many sites use
    /topics/X to list articles living under /news/Y or /features/Z.
    Other links = navigation/section links, which go to the regular queue.
    Returns (article_url_paths, other_raw_links).
    """
    article_paths: list[str] = []
    other_links: list[str] = []
    seen_paths: set[str] = set()

    for link in links:
        path = _normalize_link_to_path(link, current_path, config)
        if path is None:
            other_links.append(link)
            continue

        if path in visited or path in seen_paths:
            continue
        seen_paths.add(path)

        if not is_in_scope(path, config):
            continue

        if has_article_slug(path):
            article_paths.append(path)
        else:
            other_links.append(link)

    return article_paths, other_links


def _queue_links(
    links: list[str],
    current_path: str,
    config: ScrapeConfig,
    visited: set[str],
    visited_hashes: set[str],
    to_visit: deque[str],
    hash_based_site: bool,
    discovery_map: DiscoveryMap | None = None,
    cutoff: datetime | None = None,
):
    """Process extracted links and add valid ones to the crawl queue."""
    for link in links:
        if not link:
            continue
        if link.startswith(("mailto:", "tel:", "javascript:", "data:")):
            continue
        # Protocol-relative URLs are external
        if link.startswith("//"):
            continue

        # Handle hash-only links
        if link.startswith("#"):
            is_page_hash = (
                re.match(r'^#t=.+\.htm', link)
                or re.match(r'^#topic=', link)
                or re.match(r'^#.*\.htm$', link)
            )
            if is_page_hash and (hash_based_site or config.js_render):
                hash_url = config.base_url + current_path + link
                if link not in visited_hashes and f"{current_path}{link}" not in visited:
                    to_visit.append(hash_url)
            continue

        # Strip query params
        clean = link.split("?")[0]

        # Keep hash for hash-based sites
        if not (hash_based_site and "#" in clean):
            clean = clean.split("#")[0]

        # Skip non-HTML files
        if SKIP_EXTENSIONS.search(clean):
            continue

        # Convert to path
        if clean.startswith(config.base_url):
            clean = clean[len(config.base_url):]
        elif clean.startswith(("http://", "https://")):
            continue  # External

        # Resolve relative
        if not clean.startswith("/") and not clean.startswith("#"):
            current_dir = current_path.rsplit("/", 1)[0] if "/" in current_path else "/"
            clean = posixpath.join(current_dir, clean)

        # Normalize ./  ../  and double slashes
        clean = posixpath.normpath(clean)
        if not clean.startswith("/"):
            clean = "/" + clean

        if clean and clean not in visited:
            # Skip non-content utility paths when date filtering
            if cutoff and NON_CONTENT_PATHS.search(clean):
                continue
            # Skip RSS/feed/atom URLs
            if _RE_RSS_FEED.search(clean):
                continue
            # Pre-filter: skip article URLs known to be old from discovery map
            # or URL date pattern. Only filter article-slug paths, section/nav
            # pages must always be queued so they can discover articles.
            if cutoff and has_article_slug(clean):
                disc_date = discovery_map.get(clean) if discovery_map else None
                if disc_date and disc_date < cutoff:
                    continue
                if url_has_old_date(clean, cutoff):
                    continue
            to_visit.append(clean)


def _extract_article_thumbnails(html: str, article_paths: list[str]) -> dict[str, str]:
    """Extract thumbnail image URLs for articles from a section page.

    Looks for <img> tags near <a> tags linking to the given article paths.
    Returns dict mapping article_path -> thumbnail_image_url.
    """
    thumbnails: dict[str, str] = {}
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return thumbnails

    article_path_set = set(article_paths)
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].split("?")[0].split("#")[0].rstrip("/")
        # Normalize relative paths
        if not href.startswith("/"):
            continue
        if href not in article_path_set:
            continue
        # Look for img inside the link
        img = a_tag.find("img", src=True)
        if not img:
            parent = a_tag.parent
            if parent:
                img = parent.find("img", src=True)
        if img and img.get("src"):
            thumbnails[href] = img["src"]
    return thumbnails


def _extract_title_from_html(html: str) -> str | None:
    """Extract page title from HTML."""
    try:
        soup = BeautifulSoup(html[:10000], "lxml")
        # Try og:title first
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            return og["content"].strip()
        # Then <title>
        title = soup.find("title")
        if title:
            text = title.get_text(strip=True)
            # Strip site name suffix (common pattern: "Article | Site Name")
            for sep in (" | ", " - ", " \u2013 ", " \u2014 "):
                if sep in text:
                    text = text.split(sep)[0].strip()
            return text
    except Exception:
        pass
    return None


def _extract_description_from_html(html: str) -> str | None:
    """Extract meta description from HTML."""
    try:
        soup = BeautifulSoup(html[:10000], "lxml")
        for attr in ("description", "og:description"):
            meta = soup.find("meta", attrs={"name": attr}) or soup.find("meta", property=attr)
            if meta and meta.get("content"):
                return meta["content"].strip()
    except Exception:
        pass
    return None


def _find_section_in_outline(outline: SiteOutline, section_path: str):
    """Find a NavSection in the outline tree by path."""
    def _search(sections):
        for s in sections:
            if s.path.rstrip("/") == section_path.rstrip("/"):
                return s
            found = _search(s.sub_sections)
            if found:
                return found
        return None
    return _search(outline.nav_sections)


def _copy_section_images(
    md_output: str,
    section_articles: dict[str, list[dict]],
    source_img_dir: str,
    verbose: bool,
) -> None:
    """Copy referenced images to each section's images/ directory.

    Scans all .md files in each section directory for image references
    (``images/<filename>`` patterns) and copies the matching files from
    the global ScrapedSites images/ directory into the section's own
    images/ subdirectory under MarkdownSites.

    This ensures that:
    - Section index.md thumbnail references resolve correctly
    - Article .md files within sections can reference images relative
      to their own directory
    """
    import os
    import shutil

    _RE_MD_IMAGE = re.compile(r'!\[[^\]]*\]\(images/([^)]+)\)')

    for sec_path, articles in section_articles.items():
        if not articles:
            continue

        section_dir = sec_path.strip("/")
        section_md_dir = (
            os.path.join(md_output, section_dir) if section_dir
            else md_output
        )

        if not os.path.isdir(section_md_dir):
            continue

        # Collect all image filenames referenced in .md files in this section
        # (uses os.walk to find articles in subdirectories)
        referenced_images: set[str] = set()
        for dirpath, _dirnames, filenames in os.walk(section_md_dir):
            # Skip the images/ subdirectory itself
            if os.path.basename(dirpath) == "images":
                continue
            for filename in filenames:
                if not filename.endswith(".md"):
                    continue
                md_filepath = os.path.join(dirpath, filename)
                try:
                    with open(md_filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                    for match in _RE_MD_IMAGE.finditer(content):
                        referenced_images.add(match.group(1))
                except OSError:
                    continue

        if not referenced_images:
            continue

        # Copy referenced images from global images/ to section images/
        section_img_dir = os.path.join(section_md_dir, "images")
        os.makedirs(section_img_dir, exist_ok=True)

        copied = 0
        for img_name in referenced_images:
            src = os.path.join(source_img_dir, img_name)
            if os.path.isfile(src):
                dst = os.path.join(section_img_dir, img_name)
                if not os.path.isfile(dst):
                    shutil.copy2(src, dst)
                    copied += 1

        if copied and verbose:
            print(f"  Copied {copied} images to {section_dir or 'root'}/images/")
