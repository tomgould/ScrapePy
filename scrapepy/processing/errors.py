"""Error page detection for pages that answered 200 (soft 404s and the like).

A response with status 400 or above is rejected by its status before these
checks run. A 200 page is an error page only when its title or its first
H1 says so (a status code at the start, or a phrase such as "Page not
found"), or when it is a short storage-service error document (NoSuchKey,
AccessDenied). Body text and code samples are never read for this, so a
tutorial that prints 404 in an example, or a chapter called "Errors and
Exceptions", is kept.
"""

import re

from bs4 import BeautifulSoup

# A storage-service (S3 style) error document is a few hundred bytes of XML
_STORAGE_ERROR_MAX = 2000
_STORAGE_MARKERS = ("NoSuchKey", "AccessDenied", "The specified key does not exist")

_CODES = r"(?:400|401|403|404|410|429|500|502|503|504)"
_ERROR_HEADING_RES = tuple(re.compile(p, re.IGNORECASE) for p in (
    # A heading that is only the code: "404", "HTTP 404", "Error 404"
    rf"^\W*(?:(?:http|error)\W*)?{_CODES}\W*$",
    # "404 Not Found", "HTTP 403: Forbidden", "Example | Error 500"
    rf"\b{_CODES}\W*(?:error|not\s+found|forbidden|gone|unauthori[sz]ed)\b",
    rf"\berror\W*{_CODES}\b",
    r"\b(?:internal\s+server\s+error|service\s+(?:temporarily\s+)?unavailable|"
    r"bad\s+gateway|gateway\s+time-?out)\b",
    # "Page not found", "File not found - Example"
    r"\b(?:page|file|document|resource|url)\s+(?:was\s+)?not\s+found\b",
    r"\b(?:page|file)\s+(?:cannot|can't|can’t|could\s+not)\s+be\s+found\b",
    r"\bpage\s+(?:does\s+not|doesn't|doesn’t)\s+exist\b",
    # A heading that is nothing but the error
    r"^\W*(?:not\s+found|access\s+denied|forbidden|unauthori[sz]ed|error|"
    r"(?:internal\s+)?server\s+error|nosuchkey)\W*$",
))


def is_error_heading(text: str) -> bool:
    """Whether a title or H1 text names an error page."""
    text = (text or "").strip()
    return bool(text) and any(r.search(text) for r in _ERROR_HEADING_RES)


def is_storage_error_document(html: str) -> bool:
    """A short storage-service error document (S3 NoSuchKey and the like)."""
    return len(html) < _STORAGE_ERROR_MAX and any(m in html for m in _STORAGE_MARKERS)


def is_error_page_html(html: str) -> bool:
    """Quick check on raw HTML: a storage error document, or a <title> or
    first <h1> that names an error."""
    if not html:
        return False
    if is_storage_error_document(html):
        return True
    soup = BeautifulSoup(html[:20000], "lxml")
    title = soup.find("title")
    h1 = soup.find("h1")
    return is_error_page_content(title.get_text() if title else "",
                                 h1.get_text() if h1 else "", "")


def is_error_page_content(title: str, h1_text: str, body_text: str = "") -> bool:
    """Whether the title or H1 names an error page. body_text is not read:
    it is accepted so callers can pass what they have."""
    return is_error_heading(title) or is_error_heading(h1_text)


def is_cloudflare_challenge(html: str) -> bool:
    """Detect Cloudflare challenge pages."""
    return "Verifying you are human" in html or "Just a moment" in html
