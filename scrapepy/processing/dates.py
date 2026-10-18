"""Multi-signal date extraction for article pages."""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum

from bs4 import BeautifulSoup


class DateSource(IntEnum):
    """Confidence ranking for date sources (higher = more confident)."""
    HTTP_HEADER = 1
    TIME_ELEMENT = 2
    URL_PATTERN = 3
    JSON_LD = 4
    META_TAG = 5


@dataclass
class DateResult:
    """A detected publish date with its source."""
    date: datetime
    source: DateSource
    raw: str  # Original string the date was parsed from


# URL date patterns — matches /2026/02/25/, /2026-02-25-, /20260225- in URL paths
_URL_DATE_PATTERNS = [
    # /2026/02/25/slug or /2026/02/25-slug
    re.compile(r'/(\d{4})/(\d{1,2})/(\d{1,2})(?:/|-)'),
    # /2026-02-25-slug or /2026-02-25/slug
    re.compile(r'/(\d{4})-(\d{1,2})-(\d{1,2})(?:/|-)'),
    # /20260225-slug or /20260225/
    re.compile(r'/(\d{4})(\d{2})(\d{2})(?:/|-)'),
]


def _parse_iso_date(text: str) -> datetime | None:
    """Parse an ISO 8601 date/datetime string into a timezone-aware datetime."""
    text = text.strip()
    if not text:
        return None
    # Try common ISO formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _parse_rfc2822_date(text: str) -> datetime | None:
    """Parse RFC 2822 date (common in HTTP headers and RSS feeds)."""
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(text.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def extract_date_from_url(url_path: str) -> DateResult | None:
    """Extract a date from URL path patterns like /2026/02/25/slug."""
    for pattern in _URL_DATE_PATTERNS:
        m = pattern.search(url_path)
        if m:
            try:
                year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 1990 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
                    dt = datetime(year, month, day, tzinfo=timezone.utc)
                    return DateResult(date=dt, source=DateSource.URL_PATTERN, raw=m.group(0))
            except ValueError:
                continue
    return None


def extract_dates_from_html(html: str) -> list[DateResult]:
    """Extract dates from HTML meta tags, JSON-LD, and <time> elements."""
    results: list[DateResult] = []

    try:
        soup = BeautifulSoup(html[:50000], "lxml")
    except Exception:
        return results

    # 1. Meta tags (highest confidence from HTML)
    meta_properties = [
        "article:published_time",
        "og:article:published_time",
        "datePublished",
        "date",
        "publish_date",
        "article:published",
        "sailthru.date",
        "parsely-pub-date",
    ]
    for prop in meta_properties:
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            dt = _parse_iso_date(tag["content"])
            if dt:
                results.append(DateResult(date=dt, source=DateSource.META_TAG, raw=tag["content"]))
                break  # One meta date is enough

    # 2. JSON-LD datePublished
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            # Handle both single objects and arrays
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                # Check both top-level and @graph items
                candidates = [item]
                if "@graph" in item and isinstance(item["@graph"], list):
                    candidates.extend(item["@graph"])
                for obj in candidates:
                    if not isinstance(obj, dict):
                        continue
                    for key in ("datePublished", "dateCreated"):
                        raw = obj.get(key)
                        if raw and isinstance(raw, str):
                            dt = _parse_iso_date(raw)
                            if dt:
                                results.append(DateResult(date=dt, source=DateSource.JSON_LD, raw=raw))
                                break
                    else:
                        continue
                    break
        except (json.JSONDecodeError, TypeError):
            continue

    # 3. <time> elements with datetime attribute
    for time_el in soup.find_all("time", attrs={"datetime": True}):
        raw = time_el["datetime"]
        dt = _parse_iso_date(raw)
        if dt:
            results.append(DateResult(date=dt, source=DateSource.TIME_ELEMENT, raw=raw))
            break  # One time element is enough

    return results


def extract_date_from_headers(headers: dict) -> DateResult | None:
    """Extract date from HTTP Last-Modified header (weakest signal)."""
    last_mod = headers.get("last-modified") or headers.get("Last-Modified")
    if last_mod:
        dt = _parse_rfc2822_date(last_mod)
        if dt:
            return DateResult(date=dt, source=DateSource.HTTP_HEADER, raw=last_mod)
    return None


def get_best_date(
    url_path: str,
    html: str | None = None,
    headers: dict | None = None,
) -> DateResult | None:
    """Get the most confident date from all available signals.

    Priority: META_TAG > JSON_LD > URL_PATTERN > TIME_ELEMENT > HTTP_HEADER
    """
    candidates: list[DateResult] = []

    # URL-based date
    url_date = extract_date_from_url(url_path)
    if url_date:
        candidates.append(url_date)

    # HTML-based dates
    if html:
        candidates.extend(extract_dates_from_html(html))

    # HTTP header date
    if headers:
        hdr_date = extract_date_from_headers(headers)
        if hdr_date:
            candidates.append(hdr_date)

    if not candidates:
        return None

    # Return highest-confidence source
    return max(candidates, key=lambda r: r.source.value)


def url_has_old_date(url_path: str, cutoff: datetime) -> bool:
    """Quick check: does this URL path contain a date that's older than cutoff?

    Used for pre-filtering URLs before fetching. Only checks URL patterns,
    not HTML content (which we don't have yet).
    """
    result = extract_date_from_url(url_path)
    if result is None:
        return False  # No date in URL — can't tell, don't filter
    return result.date < cutoff
