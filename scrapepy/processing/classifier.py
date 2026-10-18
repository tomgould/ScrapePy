"""Page classification: SECTION (navigation/index) vs ARTICLE (dated content)."""

import re
from enum import Enum

from bs4 import BeautifulSoup

from .dates import extract_date_from_url, extract_dates_from_html


class PageType(Enum):
    SECTION = "section"
    ARTICLE = "article"
    UNKNOWN = "unknown"


# URL patterns that strongly suggest a section/index/navigation page
_SECTION_URL_PATTERNS = re.compile(
    r'(?:^/(?:$|index\.html?$))'          # Homepage / index
    r'|/(?:category|categories)(?:/|$)'
    r'|/(?:tag|tags)(?:/|$)'
    r'|/(?:topic|topics)(?:/|$)'
    r'|/(?:archive|archives)(?:/|$)'
    r'|/(?:author|authors)(?:/|$)'
    r'|/(?:section|sections)(?:/|$)'
    r'|/(?:department|departments)(?:/|$)'
    r'|/(?:page/\d+)(?:/|$)'              # Pagination
    r'|/(?:news|blog|press|media|articles)/?$'  # Section roots (named)
    r'|^/[a-z][a-z0-9-]{1,20}/?$'             # Short single-segment roots: /sport, /business, /whats-on
    r'|^/[a-z][a-z0-9-]{1,20}/[a-z][a-z0-9-]{1,20}/?$'  # Two-segment section roots: /news/opinion, /sport/rugby
    r'|/(?:feed|rss|atom)(?:/|$)',
    re.IGNORECASE,
)

# URL patterns that suggest an article (date in path)
_ARTICLE_URL_PATTERNS = re.compile(
    r'/\d{4}/\d{1,2}/\d{1,2}/'     # /2026/02/25/
    r'|/\d{4}-\d{1,2}-\d{1,2}[-/]' # /2026-02-25-
    r'|/\d{8}[-/]',                 # /20260225-
    re.IGNORECASE,
)


def _compute_link_density(html: str) -> float:
    """Estimate link density: ratio of link text to total text in main content.

    Returns a value between 0 (no links) and 1 (all links).
    Higher values suggest a section/navigation page.
    """
    try:
        soup = BeautifulSoup(html[:50000], "lxml")
    except Exception:
        return 0.0

    # Remove non-content areas
    for tag in soup.find_all(["nav", "header", "footer", "aside", "script", "style", "noscript"]):
        tag.decompose()

    body = soup.find("body")
    if not body:
        return 0.0

    total_text = body.get_text(separator=" ", strip=True)
    if len(total_text) < 50:
        return 0.0

    # Sum text inside <a> tags
    link_text_len = sum(len(a.get_text(strip=True)) for a in body.find_all("a"))

    return min(link_text_len / len(total_text), 1.0)


def is_section_url(url_path: str) -> bool:
    """Quick check if a URL path looks like a section/navigation page."""
    return bool(_SECTION_URL_PATTERNS.search(url_path))


def classify_page(url_path: str, html: str) -> PageType:
    """Classify a page as SECTION, ARTICLE, or UNKNOWN.

    Section pages (navigation/index) are always kept during date filtering.
    Articles are filtered by publish date. Unknown pages are kept conservatively.
    """
    # 1. Strong URL signals
    if _SECTION_URL_PATTERNS.search(url_path):
        return PageType.SECTION

    if _ARTICLE_URL_PATTERNS.search(url_path):
        return PageType.ARTICLE

    # 2. Check for date signals in HTML
    has_date = bool(extract_date_from_url(url_path)) or bool(extract_dates_from_html(html))

    # 3. Link density analysis
    link_density = _compute_link_density(html)

    # High link density + no date signals = section page
    if link_density > 0.4 and not has_date:
        return PageType.SECTION

    # Has date signals + lower link density = article
    if has_date and link_density < 0.5:
        return PageType.ARTICLE

    # Has date signals but also high link density, still article
    # (some articles have lots of related links)
    if has_date:
        return PageType.ARTICLE

    return PageType.UNKNOWN
