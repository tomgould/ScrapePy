"""URL normalization, scope checking, and link extraction."""

import posixpath
import re
from urllib.parse import urlparse, urljoin

from ..config import ScrapeConfig

# File extensions to skip when crawling
SKIP_EXTENSIONS = re.compile(
    r'\.(pdf|doc|docx|jpg|jpeg|png|gif|svg|webp|ico|css|js|woff|woff2|ttf|eot|mp4|mp3|wav|webm|zip|tar|gz'
    # Data formats: a page's JSON, XML or feed version is never a page
    r'|json|xml|rss|atom)$',
    re.IGNORECASE,
)


def is_in_scope(url_path: str, config: ScrapeConfig) -> bool:
    if not config.stay_in_path:
        return True
    if not config.starting_path:
        return True

    normalised = url_path.split("?")[0].split("#")[0]

    if config.path_wildcard:
        return config.starting_path in normalised

    return (
        normalised == config.starting_path
        or normalised.startswith(config.starting_path + "/")
    )


def normalize_link(link: str, current_path: str, base_url: str) -> str | None:
    """Normalize a link to an absolute path on the same domain. Returns None for external/skip links."""
    if not link:
        return None

    # Skip non-page links
    if link.startswith(("mailto:", "tel:", "javascript:", "data:", "blob:", "about:")):
        return None

    # Strip query and fragment for crawl purposes
    clean = link.split("?")[0].split("#")[0]
    if not clean:
        return None

    # Skip non-HTML file extensions
    if SKIP_EXTENSIONS.search(clean):
        return None

    # Convert absolute URL to path
    if clean.startswith(base_url):
        clean = clean[len(base_url):]
    elif clean.startswith("http://") or clean.startswith("https://"):
        # External link
        return None

    # Resolve relative links
    if not clean.startswith("/"):
        current_dir = current_path.rsplit("/", 1)[0] if "/" in current_path else "/"
        clean = posixpath.join(current_dir, clean)

    # Normalize the path
    clean = posixpath.normpath(clean)
    if not clean.startswith("/"):
        clean = "/" + clean

    return clean


def extract_links_from_html(html: str, base_url: str) -> list[str]:
    """Quick regex-based link extraction from raw HTML (used as fallback)."""
    links = set()

    # href attributes
    for m in re.finditer(r'href=["\']([^"\']+)["\']', html):
        links.add(m.group(1))

    # data-href, data-url attributes
    for attr in ("data-href", "data-url", "data-link"):
        for m in re.finditer(rf'{attr}=["\']([^"\']+)["\']', html):
            links.add(m.group(1))

    return list(links)


# Non-content utility paths to skip (login, cart, admin, etc.).
# Used by crawl.py (date filtering) and navigation.py (nav link filtering).
NON_CONTENT_PATHS = re.compile(
    r'/(?:login|log-in|signin|sign-in|signup|sign-up|register|logout)(?:/|$)'
    r'|/(?:settings|account|preferences|notifications|allactivity)(?:/|$)'
    r'|/(?:cart|checkout|wishlist|order|orders)(?:/|$)'
    r'|/(?:admin|wp-admin|wp-login|dashboard)(?:/|$)'
    r'|/(?:share|embed|print)(?:/|$)',
    re.IGNORECASE,
)

# Paths that are never editorial sections (superset of NON_CONTENT_PATHS).
_NON_SECTION_PATHS = re.compile(
    r'^/(?:author|authors|topic|topics|tag|tags)/'
    r'|/(?:rss|feed|atom)(?:\.xml)?/?$'
    r'|/rss\w+(?:/|$)'
    r'|^/(?:page|p)/\d+'
    r'|/(?:login|log-in|signin|sign-in|signup|sign-up|register|logout)(?:/|$)'
    r'|/(?:settings|account|preferences|notifications|allactivity)(?:/|$)'
    r'|/(?:cart|checkout|wishlist|order|orders)(?:/|$)'
    r'|/(?:admin|wp-admin|wp-login|dashboard)(?:/|$)'
    r'|/(?:share|embed|print)(?:/|$)'
    r'|^/cdn-cgi/'
    r'|\.(?:json|xml|txt)$'
    r'|/(?:contact|about|privacy|cookie|gdpr|accessibility)(?:-|/|$)'
    r'|/(?:subscribe|subscriptions|newsletter|advertise)(?:-|/|$)'
    r'|/(?:search|sitemap|gender-pay|archive)(?:-|/|$|\.)'
    r'|/(?:terms|conditions)(?:-|/|$)'
    r'|terms.conditions'
    # ISO 639-1 language codes as top-level path segments, with optional
    # locale suffix like -us, -br, -hans (e.g., /en-us, /pt-br, /zh-hans)
    r'|^/(?:af|am|ar|az|be|bg|bn|bs|ca|cs|cy|da|de|el|en|es|et|eu|fa|fi|fr'
    r'|ga|gl|gu|ha|he|hi|hr|hu|hy|id|ig|is|it|ja|ka|kk|km|kn|ko|ku|ky|lb'
    r'|lo|lt|lv|mg|mi|mk|ml|mn|mr|ms|mt|my|nb|ne|nl|nn|no|ny|or|pa|pl|ps'
    r'|pt|ro|ru|rw|sd|si|sk|sl|so|sq|sr|sv|sw|ta|te|tg|th|ti|tk|tl|tr|tt'
    r'|uk|ur|uz|vi|xh|yo|zh|zu)(?:[-_][a-z]{2,4})?(?:/|$)',
    re.IGNORECASE,
)


def has_article_slug(path: str) -> bool:
    """Check if a URL path ends with an article-like slug (long, hyphenated).

    Article slugs: garmin-catalyst-2-track-driving-coach, new-car-review
    Non-article segments: reviews, gaming, authors, 10757119
    """
    if not path:
        return False
    last_segment = path.rstrip("/").rsplit("/", 1)[-1]
    if not last_segment:
        return False
    # Long slug with hyphens
    if "-" in last_segment and len(last_segment) >= 15:
        return True
    # Multiple hyphens even if shorter
    if last_segment.count("-") >= 2:
        return True
    return False


def is_likely_article_path(path: str) -> bool:
    """Conservative article path detection for section discovery.

    Stricter than has_article_slug to avoid false positives
    on section names like 'arts-and-entertainment' (only 2 hyphens).
    """
    if not path:
        return False
    last = path.rstrip("/").rsplit("/", 1)[-1]
    if not last:
        return False
    # 3+ hyphens, very likely article headline slug
    if last.count("-") >= 3:
        return True
    # Long slug (>= 30 chars) with hyphens
    if "-" in last and len(last) >= 30:
        return True
    # Ends with numeric CMS ID (slug-12345)
    if re.search(r'-\d{4,}$', last):
        return True
    return False


def is_non_section_path(url_path: str) -> bool:
    """Check if a URL path is a known non-section pattern (topic, tag, author, etc.)."""
    return bool(_NON_SECTION_PATHS.search(url_path))


def discover_section_pages(html: str, base_url: str) -> list[str]:
    """Extract section-like pages from homepage HTML.

    Takes all internal links, filters out article paths and non-section
    pages (author, topic, tag, utility, RSS), returns deduplicated sorted
    list of section paths.
    """
    seen: set[str] = set()
    sections: list[str] = []

    for m in re.finditer(r'href=["\']([^"\']+)["\']', html):
        href = m.group(1)
        if href.startswith(("mailto:", "tel:", "javascript:", "#", "data:", "//")):
            continue

        clean = href.split("?")[0].split("#")[0]
        if not clean:
            continue
        if SKIP_EXTENSIONS.search(clean):
            continue

        # Convert to path
        if clean.startswith(base_url):
            clean = clean[len(base_url):]
        elif clean.startswith(("http://", "https://")):
            continue  # External

        if not clean.startswith("/"):
            clean = "/" + clean
        clean = posixpath.normpath(clean)
        if not clean.startswith("/"):
            clean = "/" + clean

        normalized = clean.rstrip("/")
        if not normalized:
            continue

        if is_likely_article_path(normalized):
            continue
        if _NON_SECTION_PATHS.search(normalized):
            continue

        if normalized not in seen:
            seen.add(normalized)
            sections.append(normalized)

    sections.sort()
    return sections


def classify_asset_url(url: str, base_url: str, include_assets: bool) -> dict | None:
    """Classify a URL into an asset category and return download info.

    Returns dict with keys: folder, filename, full_url, or None to skip.
    """
    if not url or url.startswith(("data:", "javascript:", "blob:", "about:")):
        return None

    # Resolve relative URLs
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = base_url + url
    elif not url.startswith("http"):
        url = base_url + "/" + url

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    if parsed.scheme not in ("http", "https"):
        return None

    pathname = parsed.path
    ext = ""
    if "." in pathname.split("/")[-1]:
        ext = "." + pathname.split("/")[-1].rsplit(".", 1)[1].lower()

    IMAGES = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tiff"}
    CSS = {".css"}
    JS = {".js"}
    FONTS = {".woff", ".woff2", ".ttf", ".eot", ".otf"}
    MEDIA = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".mp4", ".mp3", ".wav", ".webm"}

    if ext in IMAGES:
        folder = "images"
    elif ext in CSS:
        if not include_assets:
            return None
        folder = "css"
    elif ext in JS:
        if not include_assets:
            return None
        folder = "js"
    elif ext in FONTS:
        if not include_assets:
            return None
        folder = "fonts"
    elif ext in MEDIA:
        folder = "media"
    else:
        if not include_assets:
            return None
        folder = "media"

    filename = pathname.split("/")[-1] or "file"
    filename = filename.split("?")[0]
    filename = re.sub(r'[<>:"|?*]', "_", filename)

    # Truncate very long filenames
    if len(filename) > 100:
        import hashlib
        h = hashlib.md5(url.encode()).hexdigest()[:12]
        file_ext = ext or ""
        prefix = re.sub(r'[^a-zA-Z0-9_-]', '_', filename[:30])
        filename = f"{prefix}_{h}{file_ext}"

    return {"folder": folder, "filename": filename, "full_url": url}
