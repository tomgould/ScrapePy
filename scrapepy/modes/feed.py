"""Feed mode: one Markdown file per RSS 2.0 or Atom entry, from one request.

The feed URL is checked against robots.txt like a single page and fetched
once, after the run's pacing gap. Entry links are not followed: the body
comes from the feed (content:encoded, Atom content, else the summary).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import posixpath
import re
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..config import ScrapeConfig
from ..convert.html_prep import absolutize_links, fix_block_links, normalize_headings
from ..convert.to_markdown import (
    HTMLToMarkdown,
    _format_visible_date,
    build_frontmatter,
    content_hash,
    utc_now_iso,
)
from ..engine.fetcher import fetch_page_http
from ..engine.threads import run_blocking
from ..output.run_summary import RunStats, build_summary
from ..output.writer import url_to_filepath, write_file
from ..processing.discovery import load_robots_policy
from ..processing.feeds import FeedEntry, decode_feed, is_teaser, parse_feed
from ..processing.robots import HostRateLimiter, RobotsPolicy
from .crawl import _note_interrupt, _print_robots_status, _write_summary_quietly

# Entries written when --max-articles is not given
DEFAULT_FEED_ENTRIES = 50
NOT_A_FEED = "not an RSS or Atom feed"
UNREADABLE_ENTRIES = "the feed has entries but none could be read"
_RE_ENTRY_TAG = re.compile(r"<(?:\w+:)?(?:item|entry)[\s>/]", re.IGNORECASE)


def _fetch(url: str, config: ScrapeConfig) -> tuple[int, bytes, str | None, str | None]:
    """(status, body bytes, HTTP encoding, error). Status 0 on a network error."""
    try:
        response = fetch_page_http(url, config)
        body = response.body
        if isinstance(body, str):
            body = body.encode(response.encoding or "utf-8")
        return response.status, body or b"", response.encoding, None
    except Exception as e:
        return 0, b"", None, f"{type(e).__name__}: {e}"


def entry_stem(link: str, config: ScrapeConfig) -> str:
    """Path for an entry without the .md, from its link like a crawled
    page's file name. A query string is kept in the name (feeds often link
    ?p=123) and an entry on another host goes under a folder named after
    that host."""
    parsed = urlparse(link)
    path = posixpath.normpath(parsed.path or "/")
    if not path.startswith("/"):
        path = "/" + path
    rel = os.path.relpath(url_to_filepath(path, config), config.output_dir)
    stem = os.path.splitext(rel)[0]
    if parsed.query:
        query = re.sub(r"[^A-Za-z0-9]+", "-", parsed.query).strip("-")[:80]
        if query:
            stem += "-" + query
    host = parsed.netloc.lower().removeprefix("www.")
    if host and host != config.domain.lower().removeprefix("www."):
        stem = os.path.join(host.replace(".", "_"), stem)
    return stem


def entry_output_paths(links: list[str], config: ScrapeConfig) -> dict[str, str]:
    """Markdown path for each link. Links whose names would collide all get
    a suffix from a short hash of the link, so a name never depends on the
    order of the feed."""
    stems = {link: entry_stem(link, config) for link in links}
    counts: dict[str, int] = {}
    for stem in stems.values():
        counts[stem] = counts.get(stem, 0) + 1
    paths = {}
    for link, stem in stems.items():
        if counts[stem] > 1:
            stem += "-" + hashlib.sha256(link.encode("utf-8")).hexdigest()[:8]
        paths[link] = os.path.join(config.markdown_dir, stem + ".md")
    return paths


def entry_markdown(entry: FeedEntry, converter: HTMLToMarkdown, base_url: str) -> tuple[str, int]:
    """(Markdown page for one entry, word count of its body)."""
    body_md = ""
    if entry.body_html.strip():
        soup = BeautifulSoup(entry.body_html, "lxml")
        for tag in soup.find_all(["script", "style", "iframe", "noscript", "form"]):
            tag.decompose()
        fix_block_links(soup)
        absolutize_links(soup, base_url)
        root = soup.body or soup
        raw = converter._md(root.decode_contents())
        raw = converter.promote_bold_headings(raw)
        body_md = converter.clean_markdown(raw) or re.sub(r"\n{3,}", "\n\n", raw).strip()
    title = entry.title or entry.link
    md = normalize_headings(f"# {title}\n\n{body_md}".rstrip() + "\n")
    words = HTMLToMarkdown._compute_word_count(body_md) if body_md else 0
    return md, words


def _existing_hash(path: str) -> str | None:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^content_hash: (\S+)$", text.split("\n---\n", 1)[0], re.MULTILINE)
    return m.group(1) if m else None


async def run_feed_mode(config: ScrapeConfig, explicit: bool = True) -> dict | None:
    """Fetch the feed, write its entries and return the run summary.

    With explicit=False (the URL only looked like a feed) a response that is
    not a feed returns None, so the caller can crawl it as a site instead.
    """
    stats = RunStats()
    os.makedirs(config.output_dir, exist_ok=True)
    _write_summary_quietly(config, build_summary(config, stats, in_progress=True))
    url = config.url
    print(f"Feed mode: {url}")
    if config.has_date_filter:
        print("  (--recent/--since have no effect in feed mode; all entries up to the cap are kept)")
    info = {
        "feed_url": url,
        "format": None,
        "title": None,
        "entries_in_feed": 0,
        "entries_written": 0,
        "entries_unchanged": 0,
        "entries_over_cap": 0,
        "entries_skipped": [],
        "teaser_entries": 0,
        "teaser_urls": [],
    }
    stats.feed = info
    try:
        try:
            robots, _ = await run_blocking(load_robots_policy, config)
        except asyncio.CancelledError:
            _note_interrupt(stats)
            robots = RobotsPolicy.blocked("interrupted before robots.txt was read",
                                          base_url=config.base_url)
        _print_robots_status(robots, config)
        if stats.interrupted:
            pass
        elif not robots.can_fetch(url):
            stats.record_blocked(url)
            print(f"  {robots.note or 'Blocked by robots.txt'}: {url}")
        else:
            limiter = HostRateLimiter(robots.min_delay(config.wait_time))
            limiter.reserve(urlparse(config.base_url).netloc)  # the robots.txt request
            await limiter.wait(url)
            print(f"Fetching feed: {url}")
            status, data, encoding, error = await run_blocking(_fetch, url, config)
            stats.fetched += 1
            if error:
                stats.record_failed(url, error)
                print(f"  Failed: {error}")
            elif status != 200:
                stats.record_failed(url, f"HTTP {status}")
                print(f"  Failed: HTTP {status}")
            else:
                text = decode_feed(data, encoding)
                feed = parse_feed(text, url)
                if feed is None:
                    if not explicit:
                        return None
                    stats.record_failed(url, NOT_A_FEED)
                    print(f"  Failed: {NOT_A_FEED}")
                elif not feed.entries and _RE_ENTRY_TAG.search(text):
                    info["format"], info["title"] = feed.kind, feed.title or None
                    info["error"] = UNREADABLE_ENTRIES
                    stats.record_failed(url, UNREADABLE_ENTRIES)
                    print(f"  Failed: {UNREADABLE_ENTRIES}")
                else:
                    _write_entries(config, feed, stats, info)
                    if feed.entries and not (info["entries_written"] or info["entries_unchanged"]):
                        reasons = sorted({s["reason"] for s in info["entries_skipped"]})
                        info["error"] = ("no entry could be written ("
                                         + (", ".join(reasons) or "unknown") + ")")
                        stats.record_failed(url, info["error"])
                        print(f"  Failed: {info['error']}")
    except asyncio.CancelledError:
        _note_interrupt(stats)

    print("\n--- Feed Interrupted ---" if stats.interrupted else "\n--- Feed Complete ---")
    print(f"Entries in feed: {info['entries_in_feed']}")
    print(f"Entries written: {info['entries_written']}")
    if info["entries_unchanged"]:
        print(f"Entries unchanged: {info['entries_unchanged']}")
    if info["entries_over_cap"]:
        print(f"Entries over the cap (not written): {info['entries_over_cap']}")
    if info["entries_skipped"]:
        print(f"Entries skipped: {len(info['entries_skipped'])}")
    if info["teaser_entries"]:
        print(f"Teaser entries (short excerpts only): {info['teaser_entries']}")
    summary = build_summary(config, stats)
    summary_path = _write_summary_quietly(config, summary)
    if summary_path:
        print(f"Run summary: {summary_path}")
    return summary


def _write_entries(config: ScrapeConfig, feed, stats: RunStats, info: dict) -> None:
    info["format"] = feed.kind
    info["title"] = feed.title or None
    info["entries_in_feed"] = len(feed.entries)
    cap = config.max_articles if config.max_articles > 0 else DEFAULT_FEED_ENTRIES
    converter = HTMLToMarkdown(config.output_dir, output_dir=config.markdown_dir,
                               base_url=config.base_url, domain=config.domain,
                               absolute_links=True)
    os.makedirs(config.markdown_dir, exist_ok=True)
    seen_links: set[str] = set()
    chosen = []
    for index, entry in enumerate(feed.entries):
        if index >= cap:
            info["entries_over_cap"] += 1
            continue
        if not entry.link:
            info["entries_skipped"].append({"title": entry.title, "reason": "no link"})
            continue
        if entry.link in seen_links:
            info["entries_skipped"].append({"title": entry.title, "url": entry.link,
                                            "reason": "duplicate link"})
            continue
        seen_links.add(entry.link)
        chosen.append(entry)
    paths = entry_output_paths([e.link for e in chosen], config)
    for entry in chosen:
        md, words = entry_markdown(entry, converter, entry.link)
        if is_teaser(entry, words):
            info["teaser_entries"] += 1
            info["teaser_urls"].append(entry.link)
        metadata = {
            "title": entry.title or entry.link,
            "url": entry.link,
            "page_type": "feed_entry",
            "domain": config.domain,
        }
        if entry.date:
            metadata["date"] = entry.date
            metadata["date_source"] = "FEED"
            md = re.sub(r"^(# .+\n)", lambda m: m.group(1) + "\n**"
                        + _format_visible_date(metadata["date"]) + "**\n", md, count=1)
        if entry.author:
            metadata["author"] = entry.author
        metadata["word_count"] = HTMLToMarkdown._compute_word_count(md)
        metadata["scraped_at"] = utc_now_iso()
        metadata["content_hash"] = content_hash(md)
        path = paths[entry.link]
        existed = os.path.exists(path)
        if existed and _existing_hash(path) == metadata["content_hash"]:
            stats.unchanged.append(entry.link)
            info["entries_unchanged"] += 1
            print(f"  Unchanged: {entry.link}")
            continue
        write_file(path, build_frontmatter(metadata) + "\n" + md)
        stats.record_saved(entry.link, existed, path)
        info["entries_written"] += 1
        print(f"  Wrote: {os.path.relpath(path, config.markdown_dir)}")
