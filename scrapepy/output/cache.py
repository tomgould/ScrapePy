"""Crawl cache for incremental mode, tracks ETags, Last-Modified, and content hashes."""

import hashlib
import json
import os
from datetime import datetime, timezone


CACHE_FILENAME = ".scrapepy_cache.json"
CACHE_VERSION = 1


def _get_header(headers: dict, name: str) -> str:
    """Case-insensitive header lookup. Returns empty string if not found."""
    # Try exact lowercase first (most common from httpx/scrapling)
    val = headers.get(name.lower())
    if val:
        return val
    # Fall back to case-insensitive scan
    name_lower = name.lower()
    for k, v in headers.items():
        if k.lower() == name_lower:
            return v
    return ""


class CrawlCache:
    """Persists page/asset metadata to skip unchanged resources on re-crawl."""

    def __init__(self, output_dir: str):
        self.cache_path = os.path.join(output_dir, CACHE_FILENAME)
        self._data: dict = {
            "version": CACHE_VERSION,
            "pages": {},
            "assets": {},
        }

    def load(self):
        """Load cache from disk. Warns if file exists but cannot be loaded."""
        if not os.path.exists(self.cache_path):
            return
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") == CACHE_VERSION:
                self._data = data
            else:
                print(f"  Cache version mismatch (expected {CACHE_VERSION}, got {data.get('version')}), starting fresh.")
        except json.JSONDecodeError:
            print(f"  Warning: Cache file is corrupt, starting fresh: {self.cache_path}")
        except OSError as e:
            print(f"  Warning: Could not read cache ({e}), starting fresh.")

    def save(self):
        """Write cache to disk. Warns on failure instead of crashing."""
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, separators=(",", ":"))
        except OSError as e:
            print(f"  Warning: Could not save cache ({e})")
        except TypeError as e:
            print(f"  Warning: Cache contains non-serializable data ({e})")

    # --- Pages ---

    def get_page_headers(self, url_path: str) -> dict:
        """Return conditional request headers for a cached page, or empty dict."""
        entry = self._data["pages"].get(url_path)
        if not entry:
            return {}
        headers = {}
        if entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = entry["last_modified"]
        return headers

    def update_page(
        self,
        url_path: str,
        headers: dict,
        content: str,
        links: list[str] | None = None,
        published_date: str | None = None,
        page_type: str | None = None,
        next_links: list[str] | None = None,
    ):
        """Store cache entry for a page using response headers + content hash."""
        entry = {
            "etag": _get_header(headers, "etag"),
            "last_modified": _get_header(headers, "last-modified"),
            "content_hash": f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}",
            "links": links or [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if published_date:
            entry["published_date"] = published_date
        if page_type:
            entry["page_type"] = page_type
        if next_links is not None:
            entry["next"] = list(next_links)
        self._data["pages"][url_path] = entry

    def has_page(self, url_path: str) -> bool:
        """True when an earlier run cached this page."""
        return url_path in self._data["pages"]

    def has_page_next(self, url_path: str) -> bool:
        """Whether the page's entry records its rel="next" paths (even none)."""
        return "next" in (self._data["pages"].get(url_path) or {})

    def set_page_next(self, url_path: str, next_links: list[str]) -> None:
        entry = self._data["pages"].get(url_path)
        if entry is not None:
            entry["next"] = list(next_links)

    def get_page_next(self, url_path: str) -> list[str]:
        """The page's cached rel="next" paths, or an empty list."""
        entry = self._data["pages"].get(url_path) or {}
        return list(entry.get("next", []))

    def get_page_links(self, url_path: str) -> list[str]:
        """Return cached links for a page, or empty list."""
        entry = self._data["pages"].get(url_path)
        if not entry:
            return []
        return entry.get("links", [])

    def is_page_unchanged(self, url_path: str, content: str) -> bool:
        """Check if page content matches the cached hash (fallback for servers without conditional headers)."""
        entry = self._data["pages"].get(url_path)
        if not entry or not entry.get("content_hash"):
            return False
        current_hash = f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"
        return current_hash == entry["content_hash"]

    def get_page_date(self, url_path: str) -> str | None:
        """Return cached published_date for a page, or None."""
        entry = self._data["pages"].get(url_path)
        if not entry:
            return None
        return entry.get("published_date")

    def get_page_type(self, url_path: str) -> str | None:
        """Return cached page_type for a page, or None."""
        entry = self._data["pages"].get(url_path)
        if not entry:
            return None
        return entry.get("page_type")

    def remove_page(self, url_path: str) -> None:
        """Forget a page, so the next run fetches and converts it in full."""
        self._data["pages"].pop(url_path, None)

    def clear_pages(self) -> None:
        """Forget every page, so the next run converts all of them."""
        self._data["pages"] = {}

    # --- File assignments ---

    def has_assignments(self) -> bool:
        """False for a cache that holds no file assignments."""
        return "assignments" in self._data

    def get_assignments(self) -> dict[str, dict]:
        """URL -> its files (paths relative to the cache's folder)."""
        return {u: dict(a) for u, a in self._data.get("assignments", {}).items()}

    def set_assignments(self, assignments: dict[str, dict]) -> None:
        self._data["assignments"] = assignments

    # --- Assets ---

    def get_asset_headers(self, asset_url: str) -> dict:
        """Return conditional request headers for a cached asset, or empty dict."""
        entry = self._data["assets"].get(asset_url)
        if not entry:
            return {}
        headers = {}
        if entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = entry["last_modified"]
        return headers

    def update_asset(self, asset_url: str, headers: dict):
        """Store cache entry for an asset using response headers."""
        self._data["assets"][asset_url] = {
            "etag": _get_header(headers, "etag"),
            "last_modified": _get_header(headers, "last-modified"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def has_asset(self, asset_url: str) -> bool:
        """Check if an asset exists in the cache."""
        return asset_url in self._data["assets"]
