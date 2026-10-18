"""Error page detection (404, 403, etc.)."""

import re

ERROR_PATTERNS = ("404", "403", "401", "500", "502", "503", "not found", "forbidden", "nosuchkey", "error")


def is_error_page_html(html: str) -> bool:
    """Quick check on raw HTML for error page markers."""
    if any(marker in html for marker in ("NoSuchKey", "AccessDenied", ">404<", ">Not Found<")):
        return True
    if "The specified key does not exist" in html and len(html) < 2000:
        return True
    return False


def is_error_page_content(title: str, h1_text: str, body_text: str) -> bool:
    """Check rendered page content for error patterns."""
    title_lower = title.lower()
    h1_lower = h1_text.lower()
    body_lower = body_text.lower()

    if any(p in title_lower for p in ERROR_PATTERNS):
        return True
    if any(p in h1_lower for p in ERROR_PATTERNS):
        return True
    if "nosuchkey" in body_lower or "the specified key does not exist" in body_lower:
        return True
    if len(body_lower) < 1000 and any(
        m in body_lower for m in (
            "not found", "page not found", "file not found",
            "the page you requested", "does not exist", "access denied",
        )
    ):
        return True

    return False


def is_cloudflare_challenge(html: str) -> bool:
    """Detect Cloudflare challenge pages."""
    return "Verifying you are human" in html or "Just a moment" in html
