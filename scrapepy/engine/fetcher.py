"""Fetcher selection and session management using Scrapling."""

import asyncio
from typing import Callable
from urllib.parse import urlsplit

from scrapling import Fetcher, StealthyFetcher

from ..config import ScrapeConfig
from ..proxy import protect_logs, redact
from .redirects import (
    MAX_REDIRECTS, REDIRECT_STATUSES, RedirectRefused, check_redirect, header_value,
)

# User agent for asset downloads when --user-agent is not set. Page fetches
# without --user-agent use the browser-like headers Scrapling generates.
DEFAULT_ASSET_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def asset_user_agent(config: ScrapeConfig) -> str:
    return config.user_agent or DEFAULT_ASSET_USER_AGENT


def page_headers(config: ScrapeConfig, headers: dict | None = None) -> dict:
    """Headers for an HTTP page fetch: auth, --user-agent, then per-request headers."""
    merged = dict(config.auth_header)
    if config.user_agent:
        merged["User-Agent"] = config.user_agent
    merged.update(headers or {})
    return merged


def fetch_page_http(
    url: str, config: ScrapeConfig, headers: dict | None = None,
    follow_redirects: bool = True,
    timeout: float = 30,
    retries: int | None = None,
):
    """Fetch a page using Scrapling's HTTP-only Fetcher (fast, no browser).

    retries=None keeps Scrapling's default (3 attempts); 1 means a single attempt.
    With --refuse-private-redirects, redirects are followed here, hop by hop,
    and one into a private network raises RedirectRefused.
    """
    merged = page_headers(config, headers)
    kwargs = {}
    if retries is not None:
        kwargs["retries"] = retries
    if config.proxy:
        protect_logs(config.proxy)
        kwargs["proxy"] = config.proxy
    if follow_redirects and config.refuse_private_redirects:
        return _get_checking_redirects(url, config, merged, timeout, kwargs)
    try:
        return Fetcher.get(
            url,
            stealthy_headers=True,
            follow_redirects=follow_redirects,
            timeout=timeout,
            headers=merged,
            **kwargs,
        )
    except LookupError:
        # Scrapling parses the body with the charset the Content-Type header
        # names and raises on one Python does not know (charset=klingon).
        # The page is requested once more with a plain client, so its raw
        # body can be decoded by processing/encoding.py.
        return _fetch_raw(url, config, merged, follow_redirects, timeout)


class RawResponse:
    """The parts of a Scrapling response the crawl reads, from httpx."""

    def __init__(self, response):
        self.url = str(response.url)
        self.status = response.status_code
        self.body = response.content
        self.headers = dict(response.headers)
        # Not a usable codec name: decode_body() starts with UTF-8
        self.encoding = response.charset_encoding


def _fetch_raw(url: str, config: ScrapeConfig, headers: dict,
               follow_redirects: bool, timeout: float) -> RawResponse:
    import httpx

    with httpx.Client(follow_redirects=follow_redirects, timeout=timeout,
                      proxy=config.proxy, headers={"User-Agent": asset_user_agent(config),
                                                   **headers}) as client:
        return RawResponse(client.get(url))


def _get_checking_redirects(url: str, config: ScrapeConfig, headers: dict,
                            timeout: float, kwargs: dict):
    """Fetcher.get() following at most MAX_REDIRECTS redirects itself, each
    target checked first. Authorization is not sent to another host."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        response = Fetcher.get(
            current, stealthy_headers=True, follow_redirects=False,
            timeout=timeout, headers=headers, **kwargs,
        )
        location = header_value(getattr(response, "headers", {}), "location")
        if response.status not in REDIRECT_STATUSES or not location:
            return response
        target = check_redirect(current, location, config.url)
        if _netloc(target) != _netloc(current):
            headers = {k: v for k, v in headers.items() if k.lower() != "authorization"}
        current = target
    raise RedirectRefused(f"more than {MAX_REDIRECTS} redirects")


def _netloc(url: str) -> str:
    return urlsplit(url).netloc.lower()


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
        solve_cloudflare=config.solve_cloudflare,
    )
    if config.auth_header:
        kwargs["extra_headers"] = config.auth_header
    if config.proxy:
        protect_logs(config.proxy)
        kwargs["proxy"] = config.proxy
    if config.user_agent:
        kwargs["useragent"] = config.user_agent
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
        "User-Agent": asset_user_agent(config),
        **config.auth_header,
    }
    return httpx.AsyncClient(
        follow_redirects=True,
        timeout=30.0,
        headers=default_headers,
        proxy=config.proxy,
        **redirect_hooks(config),
    )


def redirect_hooks(config: ScrapeConfig) -> dict:
    """httpx client arguments for --refuse-private-redirects: a response hook
    that checks every redirect before the client follows it (and before a
    caller following redirects itself sees it). Empty without the flag."""
    if not config.refuse_private_redirects:
        return {}
    from .threads import run_blocking

    async def _check(response):
        location = response.headers.get("location")
        if response.status_code in REDIRECT_STATUSES and location:
            await run_blocking(check_redirect, str(response.url), location, config.url)

    return {"event_hooks": {"response": [_check]}}


async def download_document(
    url: str, config: ScrapeConfig, client, headers: dict | None = None,
    max_bytes: int = 25 * 1024 * 1024,
) -> tuple[int, bytes | None, dict, str | None]:
    """Download one document with the asset client, without following
    redirects (the caller decides whether a redirect may be followed).

    Returns (status, body, response_headers, error). body is set only for
    a 200 response no larger than max_bytes; a larger one (by Content-Length
    or while streaming, after any content decoding) returns error and no
    body. A network error returns status 0 and error.
    """
    too_large = f"larger than the {max_bytes // (1024 * 1024)} MB document size cap"
    try:
        async with client.stream("GET", url, headers=headers or {},
                                 follow_redirects=False) as resp:
            resp_headers = dict(resp.headers)
            if resp.status_code != 200:
                return resp.status_code, None, resp_headers, None
            length = resp.headers.get("content-length", "")
            if length.isdigit() and int(length) > max_bytes:
                return 200, None, resp_headers, too_large
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    return 200, None, resp_headers, too_large
            return 200, bytes(body), resp_headers, None
    except Exception as e:
        return 0, None, {}, redact(f"{type(e).__name__}: {e}", config.proxy)


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
                "User-Agent": asset_user_agent(config),
                **config.auth_header,
            }
            req_headers = {**default_headers, **(headers or {})}
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=30.0,
                headers=req_headers,
                proxy=config.proxy,
                **redirect_hooks(config),
            ) as one_shot:
                resp = await one_shot.get(url)
        resp_headers = dict(resp.headers)
        if resp.status_code == 200:
            return resp.content, 200, resp_headers
        return None, resp.status_code, resp_headers
    except Exception as e:
        if config.verbose:
            print(f"    Asset download error ({type(e).__name__}): {url}")
        return None, 0, {"error": redact(f"{type(e).__name__}: {e}", config.proxy)}
