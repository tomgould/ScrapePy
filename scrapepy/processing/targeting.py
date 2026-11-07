"""Targeted crawling: which discovered links a crawl follows.

Used by the crawl when --include, --exclude, --max-depth, --urls-file or
--follow-next is given. Without them nothing here runs and the crawl follows
links exactly as before.

Rules for a discovered link (start URLs are always fetched):
- an --exclude match skips it;
- with --include it is followed only when it is inside the --stay-in-path
  scope (when -s is given) or matches an include; without --include the
  usual scope check applies;
- with --max-depth it is followed only at depth N or less, where start URLs
  are depth 0 and each link is one deeper than the page it was found on;
- with --follow-next, pagination links (rel="next", or a path ending in
  /page/N) found on a page keep that page's depth and skip the --include
  test, so a listing's later pages are read; --exclude still applies.
"""

from __future__ import annotations

import re
from typing import Callable
from urllib.parse import urlsplit

from ..config import ScrapeConfig
from .urls import is_in_scope

_RE_REL_NEXT = re.compile(
    r"<(?:a|link)\b(?=[^>]*\brel\s*=\s*[\"']?[^\"'>]*\bnext\b)[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_RE_PAGE_PATH = re.compile(r"/page/\d+/?$", re.IGNORECASE)

SAMPLE_SIZE = 20


def start_paths_seed_only(config: ScrapeConfig) -> bool:
    """Whether only the start URLs seed the queue (no navigation seeding and,
    without a date filter, no sitemap, feed or homepage requests)."""
    return bool(config.include or config.max_depth is not None or config.urls_file)


def is_active(config: ScrapeConfig) -> bool:
    return bool(config.include or config.exclude or config.max_depth is not None
                or config.urls_file or config.follow_next)


class Targeting:
    def __init__(self, config: ScrapeConfig, normalize: Callable[[str, str], str | None]):
        self.config = config
        self.normalize = normalize                 # (link, current_path) -> path or None
        self.include = [re.compile(p) for p in config.include]
        self.exclude = [re.compile(p) for p in config.exclude]
        self.depth: dict[str, int] = {}
        self.starts: set[str] = set()
        self.pagination: set[str] = set()
        self.skipped = {"include": set(), "exclude": set(), "depth": set()}

    # -- start URLs --------------------------------------------------------

    def add_start(self, key: str) -> None:
        self.starts.add(key)
        self.depth[key] = 0

    def is_start(self, key: str) -> bool:
        return key in self.starts

    # -- discovered links --------------------------------------------------

    def note_links(self, links: list[str], html: str, current_key: str, current_path: str) -> None:
        """Record the depth of each link found on a page (one deeper than the
        page, the smallest seen wins) and, with --follow-next, which of them
        are pagination links (same depth as the page)."""
        here = self.depth.get(current_key, self.depth.get(current_path, 0))
        paging: set[str] = set()
        if self.config.follow_next:
            for href in _RE_REL_NEXT.findall(html or ""):
                path = self.normalize(href, current_path)
                if path:
                    paging.add(path)
        for link in links:
            path = self.normalize(link, current_path)
            if not path:
                continue
            if self.config.follow_next and _RE_PAGE_PATH.search(path):
                paging.add(path)
            if path in paging:
                continue
            if path not in self.depth or self.depth[path] > here + 1:
                self.depth[path] = here + 1
        for path in paging:
            self.pagination.add(path)
            if path not in self.depth or self.depth[path] > here:
                self.depth[path] = here

    def allow(self, path: str) -> str | None:
        """None when the link may be fetched, else the reason it is skipped
        ("exclude", "include", "depth" or "scope")."""
        if any(r.search(path) for r in self.exclude):
            return "exclude"
        if path not in self.pagination:
            if self.include:
                in_scope = self.config.stay_in_path and is_in_scope(path, self.config)
                if not in_scope and not any(r.search(path) for r in self.include):
                    return "include"
            elif not is_in_scope(path, self.config):
                return "scope"
        elif not self.include and not is_in_scope(path, self.config):
            return "scope"
        if self.config.max_depth is not None and self.depth.get(path, 0) > self.config.max_depth:
            return "depth"
        return None

    def record_skip(self, reason: str, path: str) -> None:
        if reason in self.skipped:
            self.skipped[reason].add(path)

    def summary(self) -> dict:
        return {f"skipped_by_{k}": len(v) for k, v in self.skipped.items()}


def read_urls_file(path: str, site_url: str | None) -> list[str]:
    """Start URLs from a file: one per line, # comments and blank lines
    ignored, http and https only, all on one site (the site of site_url when
    given, else of the first URL). Duplicates are dropped, keeping the first.
    Raises ValueError naming the line on a bad entry."""
    urls: list[str] = []
    seen: set[str] = set()
    site = urlsplit(site_url).netloc.lower() if site_url else None
    with open(path, encoding="utf-8") as f:
        for number, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = urlsplit(line)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError(f"--urls-file line {number}: not an http or https URL")
            host = parts.netloc.lower()
            if site is None:
                site = host
            elif host != site:
                raise ValueError(f"--urls-file line {number}: not on the same site as the "
                                 f"other start URLs ({site})")
            if line not in seen:
                seen.add(line)
                urls.append(line)
    if not urls:
        raise ValueError("--urls-file has no URLs")
    return urls
