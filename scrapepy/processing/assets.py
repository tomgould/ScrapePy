"""Asset discovery and concurrent downloading."""

import asyncio
import os
import re

from ..config import ScrapeConfig
from ..proxy import redact
from ..engine.fetcher import download_asset
from ..processing.urls import classify_asset_url
from ..output.writer import ensure_parent_dir

# Precompiled regexes for asset URL extraction (called per page)
_RE_IMG_SRC = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
_RE_IMG_SRCSET = re.compile(r'<img[^>]+srcset=["\']([^"\']+)["\']', re.IGNORECASE)
_RE_LINK_STYLESHEET = re.compile(
    r'<link[^>]+rel=["\']stylesheet["\'][^>]+href=["\']([^"\']+)["\']', re.IGNORECASE,
)
_RE_LINK_CSS = re.compile(r'<link[^>]+href=["\']([^"\']+\.css[^"\']*)["\']', re.IGNORECASE)
_RE_SCRIPT_SRC = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
_RE_CSS_URL = re.compile(r'url\(["\']?([^"\')\s]+)["\']?\)')


async def discover_and_download_assets(
    html: str,
    config: ScrapeConfig,
    downloaded: set[str],
    cache=None,
    http_client=None,
    images_only: bool = False,
):
    """Find asset URLs in HTML and download them concurrently."""
    asset_urls = _extract_asset_urls(html, config.include_assets)
    tasks = []

    for url in asset_urls:
        if url in downloaded:
            continue
        info = classify_asset_url(url, config.base_url, config.include_assets)
        if not info:
            continue
        if images_only and info["folder"] not in ("images", "media"):
            continue
        if info["full_url"] in downloaded:
            continue

        downloaded.add(info["full_url"])
        filepath = os.path.join(config.output_dir, info["folder"], info["filename"])
        tasks.append(
            _download_one(info["full_url"], filepath, info["filename"], config, cache, http_client)
        )

    if tasks:
        # Limit concurrency
        sem = asyncio.Semaphore(config.concurrent)

        async def _bounded(coro):
            async with sem:
                return await coro

        await asyncio.gather(*[_bounded(t) for t in tasks], return_exceptions=True)


async def _download_one(
    url: str, filepath: str, filename: str, config: ScrapeConfig, cache=None,
    http_client=None,
):
    """Download a single asset file."""
    try:
        ensure_parent_dir(filepath)

        # Send conditional headers if we have cached info and file exists on disk
        headers = None
        if cache and cache.has_asset(url) and os.path.exists(filepath):
            headers = cache.get_asset_headers(url)

        data, status, resp_headers = await download_asset(
            url, config, headers=headers, client=http_client,
        )

        if status == 304:
            print(f"  Unchanged: {filename}")
            return

        if data:
            with open(filepath, "wb") as f:
                f.write(data)
            if cache:
                cache.update_asset(url, resp_headers)
            print(f"  Downloaded: {filename}")
        else:
            print(f"  Failed: {filename} (no data)")
    except Exception as e:
        print(redact(f"  Failed: {filename} ({e})", config.proxy))


def _extract_asset_urls(html: str, include_assets: bool) -> list[str]:
    """Extract asset URLs from HTML using precompiled regexes."""
    urls = set()

    # img src
    for m in _RE_IMG_SRC.finditer(html):
        urls.add(m.group(1))

    # img srcset
    for m in _RE_IMG_SRCSET.finditer(html):
        for part in m.group(1).split(","):
            url = part.strip().split()[0]
            if url:
                urls.add(url)

    if include_assets:
        # link[rel=stylesheet]
        for m in _RE_LINK_STYLESHEET.finditer(html):
            urls.add(m.group(1))
        # Also match href before rel
        for m in _RE_LINK_CSS.finditer(html):
            urls.add(m.group(1))
        # script[src]
        for m in _RE_SCRIPT_SRC.finditer(html):
            urls.add(m.group(1))

    # Background images in inline styles
    for m in _RE_CSS_URL.finditer(html):
        url = m.group(1)
        if not url.startswith("data:"):
            urls.add(url)

    return list(urls)
