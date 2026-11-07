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


# Patterns are matched against at most this many characters of a path:
# Python regular expressions have no time limit, so this bounds the input
MAX_MATCH_CHARS = 2048
# A group with a quantifier inside that is itself repeated, such as (a+)+ or
# (\w*)*, can take exponential time on a path that almost matches
_RE_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")


def check_pattern(pattern: str) -> str | None:
    """Why a --include or --exclude pattern is refused, or None."""
    try:
        re.compile(pattern)
    except re.error as e:
        return f"invalid regular expression {pattern!r}: {e}"
    if _RE_NESTED_QUANTIFIER.search(pattern):
        return (f"regular expression {pattern!r} repeats a group that has its own "
                "quantifier, which can take exponential time; rewrite it without "
                "nested repetition")
    return None


def rel_next_paths(html: str, current_path: str,
                   normalize: Callable[[str, str], str | None]) -> list[str]:
    """Paths of a page's rel="next" links. The crawl stores them in the cache
    so a re-run answered 304 follows the same pagination."""
    out: list[str] = []
    for href in _RE_REL_NEXT.findall(html or ""):
        path = normalize(href, current_path)
        if path and path not in out:
            out.append(path)
    return out


def page_base(path: str) -> str:
    """A listing path without its /page/N suffix and trailing slash."""
    return _RE_PAGE_PATH.sub("", path).rstrip("/") or "/"


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

    # -- matching ----------------------------------------------------------

    def _matches(self, patterns, path: str) -> bool:
        target = path[:MAX_MATCH_CHARS]
        return any(r.search(target) for r in patterns)

    def _in_scope_or_included(self, path: str) -> bool:
        if self.config.stay_in_path and is_in_scope(path, self.config):
            return True
        return self._matches(self.include, path)

    # -- start URLs --------------------------------------------------------

    def add_start(self, key: str) -> None:
        self.starts.add(key)
        self.depth[key] = 0

    def is_start(self, key: str) -> bool:
        return key in self.starts

    # -- discovered links --------------------------------------------------

    def rel_next_paths(self, html: str, current_path: str) -> list[str]:
        return rel_next_paths(html, current_path, self.normalize)

    def _pagination_ok(self, path: str, current_path: str, rel_next: bool) -> bool:
        """Whether a pagination link found on current_path is followed as
        pagination: never when excluded; with -s only inside the scope or
        matching an include; otherwise when it matches an include, is the
        page's rel="next", or is a /page/N page of the same listing."""
        if self._matches(self.exclude, path):
            return False
        if self.config.stay_in_path:
            return self._in_scope_or_included(path)
        if self._matches(self.include, path) or rel_next:
            return True
        return page_base(path) == page_base(current_path)

    def note_links(self, links: list[str], html: str | None, current_key: str,
                   current_path: str, next_paths: list[str] | None = None) -> None:
        """Record the depth of each link found on a page (one deeper than the
        page; the smallest depth seen wins) and, with --follow-next, which of
        them are pagination links (same depth as the page). next_paths are
        the page's rel="next" paths, from html when not given."""
        here = self.depth.get(current_key, self.depth.get(current_path, 0))
        paging: set[str] = set()
        if self.config.follow_next:
            rel_next = next_paths if next_paths is not None else self.rel_next_paths(html, current_path)
            for path in rel_next:
                if self._pagination_ok(path, current_path, rel_next=True):
                    paging.add(path)
        for link in links:
            path = self.normalize(link, current_path)
            if not path:
                continue
            if (self.config.follow_next and path not in paging and _RE_PAGE_PATH.search(path)
                    and self._pagination_ok(path, current_path, rel_next=False)):
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
        if self._matches(self.exclude, path):
            return "exclude"
        if path not in self.pagination:
            if self.include:
                if not self._in_scope_or_included(path):
                    return "include"
            elif not is_in_scope(path, self.config):
                return "scope"
        if self.config.max_depth is not None and self.depth.get(path, 0) > self.config.max_depth:
            return "depth"
        return None

    def record_skip(self, reason: str, path: str) -> None:
        if reason in self.skipped:
            self.skipped[reason].add(path)

    def summary(self, fetched: set[str] | None = None) -> dict:
        """Skipped counts. A path skipped once and fetched later (reached
        again by a shorter route) is not counted."""
        fetched = fetched or set()
        return {f"skipped_by_{k}": len(v - fetched) for k, v in self.skipped.items()}


def _site_label(parts) -> str:
    """scheme://host[:port], never the user name or password."""
    host = (parts.hostname or "").lower()
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}"


def read_urls_file(path: str, site_url: str | None) -> list[str]:
    """Start URLs from a file: one per line, # comments and blank lines
    ignored, a UTF-8 byte order mark ignored, http and https only, all on one
    site with one scheme (that of site_url when given, else of the first
    URL). Duplicates are dropped, keeping the first. Raises ValueError naming
    the line on a bad entry; messages never show a user name or password."""
    urls: list[str] = []
    seen: set[str] = set()
    site = _site_label(urlsplit(site_url)) if site_url else None
    with open(path, encoding="utf-8-sig") as f:
        for number, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = urlsplit(line)
            try:
                label = _site_label(parts)
            except ValueError:
                raise ValueError(f"--urls-file line {number}: not a valid URL") from None
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise ValueError(f"--urls-file line {number}: not an http or https URL")
            if site is None:
                site = label
            elif label != site:
                raise ValueError(f"--urls-file line {number}: not on the same site and scheme "
                                 f"as the other start URLs ({site})")
            if line not in seen:
                seen.add(line)
                urls.append(line)
    if not urls:
        raise ValueError("--urls-file has no URLs")
    return urls
