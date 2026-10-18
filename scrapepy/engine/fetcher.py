"""Fetcher selection and session management using Scrapling."""

import asyncio
from typing import Callable

from scrapling import Fetcher, StealthyFetcher

from ..config import ScrapeConfig


def fetch_page_http(url: str, config: ScrapeConfig, headers: dict | None = None):
    """Fetch a page using Scrapling's HTTP-only Fetcher (fast, no browser)."""
    merged = {**config.auth_header, **(headers or {})}
    return Fetcher.get(
        url,
        stealthy_headers=True,
        follow_redirects=True,
        timeout=30,
        headers=merged,
    )


async def fetch_page_browser(
    url: str,
    config: ScrapeConfig,
    page_action: Callable | None = None,
    wait_time: float | None = None,
    user_data_dir: str | None = None,
):
    """Fetch a page using StealthyFetcher (Patchright browser).

    page_action receives the raw Patchright page object for custom interaction.
    user_data_dir persists cookies/localStorage across sessions (uses Patchright's
    launch_persistent_context under the hood).
    """
    wt = int(wait_time if wait_time is not None else config.wait_time)

    async def _combined_action(page):
        if page_action:
            await page_action(page)
        return page

    kwargs = dict(
        headless=config.headless,
        network_idle=True,
        page_action=_combined_action,
        timeout=120000,
        wait=wt,
        solve_cloudflare=True,
    )
    if config.auth_header:
        kwargs["extra_headers"] = config.auth_header
    if config.proxy:
        kwargs["proxy"] = config.proxy
    if user_data_dir:
        kwargs["user_data_dir"] = user_data_dir

    return await StealthyFetcher.async_fetch(url, **kwargs)


def create_asset_client(config: ScrapeConfig):
    """Create a persistent httpx.AsyncClient for asset downloads.

    Use as async context manager in the crawl loop:
        async with create_asset_client(config) as client:
            await download_asset(url, config, client=client)
    """
    import httpx

    default_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        **config.auth_header,
    }
    return httpx.AsyncClient(
        follow_redirects=True,
        timeout=30.0,
        headers=default_headers,
        proxy=config.proxy,
    )


async def download_asset(
    url: str, config: ScrapeConfig, headers: dict | None = None,
    client=None,
) -> tuple[bytes | None, int, dict]:
    """Download a single asset via HTTP. Returns (bytes, status_code, response_headers).

    If client is provided, reuses it (no per-request connection overhead).
    Otherwise creates a one-shot client (backward compatible).
    """
    import httpx

    try:
        if client:
            resp = await client.get(url, headers=headers or {})
        else:
            default_headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                **config.auth_header,
            }
            req_headers = {**default_headers, **(headers or {})}
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=30.0,
                headers=req_headers,
                proxy=config.proxy,
            ) as one_shot:
                resp = await one_shot.get(url)
        resp_headers = dict(resp.headers)
        if resp.status_code == 200:
            return resp.content, 200, resp_headers
        return None, resp.status_code, resp_headers
    except Exception as e:
        if config.verbose:
            print(f"    Asset download error ({type(e).__name__}): {url}")
        return None, 0, {"error": f"{type(e).__name__}: {e}"}
