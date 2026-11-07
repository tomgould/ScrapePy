"""--proxy: validation and keeping its credentials out of every output.

A proxy URL can carry a user name and password (http://user:pass@host:port).
Those must never appear in console output, logs, error messages or
run_summary.json, so every error text that could quote the URL goes through
redact(), and Scrapling's logger gets a filter that does the same.
"""

from __future__ import annotations

import logging
from urllib.parse import unquote, urlsplit

PROXY_SCHEMES = ("http", "https", "socks4", "socks4a", "socks5", "socks5h")
MASK = "***"


def validate_proxy(url: str) -> str:
    """The proxy URL, unchanged, or ValueError. The message never repeats
    the value given, which may hold a password."""
    try:
        parts = urlsplit((url or "").strip())
        host = parts.hostname
        parts.port  # raises ValueError for a port that is not a number
    except ValueError:
        host = None
        parts = None
    if parts is None or parts.scheme.lower() not in PROXY_SCHEMES or not host:
        raise ValueError(
            "--proxy must be a URL with a host and one of the schemes "
            + ", ".join(PROXY_SCHEMES)
            + ", for example http://proxy.example:8080"
        )
    return url.strip()


def mask_proxy(url: str | None) -> str | None:
    """The proxy URL with any user name and password replaced by ***."""
    if not url:
        return url
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    host = parts.netloc.rsplit("@", 1)[1]
    return parts._replace(netloc=f"{MASK}@{host}").geturl()


def _secrets(url: str | None) -> list[str]:
    """Strings that must not be shown: the full URL, its user info and its
    password, raw and percent-decoded. Longest first, so the whole URL is
    replaced before its parts."""
    if not url:
        return []
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return []
    userinfo = parts.netloc.rsplit("@", 1)[0]
    found = {url, userinfo, unquote(userinfo)}
    if parts.password:
        found.update({parts.password, unquote(parts.password)})
    return sorted((s for s in found if s), key=len, reverse=True)


def redact(text: str, proxy: str | None) -> str:
    """text with the proxy URL masked and its credentials removed."""
    if not text or not proxy:
        return text
    secrets = _secrets(proxy)
    if not secrets:
        return text
    masked = mask_proxy(proxy)
    for secret in secrets:
        text = text.replace(secret, masked if secret == proxy else MASK)
    return text


class _RedactFilter(logging.Filter):
    def __init__(self, proxy: str):
        super().__init__()
        self.proxy = proxy

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message, self.proxy)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        return True


_installed: set[str] = set()


def protect_logs(proxy: str | None) -> None:
    """Add a redacting filter to Scrapling's logger and its handlers, once
    per proxy URL. Does nothing when the URL holds no credentials."""
    if not proxy or proxy in _installed or not _secrets(proxy):
        return
    _installed.add(proxy)
    logger = logging.getLogger("scrapling")
    flt = _RedactFilter(proxy)
    logger.addFilter(flt)
    for handler in logger.handlers:
        handler.addFilter(flt)
