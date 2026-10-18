"""Tests for processing/dates.py — date extraction from URLs, HTML, headers."""

import pytest
from datetime import datetime, timezone

from scrapepy.processing.dates import (
    _parse_iso_date,
    _parse_rfc2822_date,
    extract_date_from_url,
    extract_dates_from_html,
    extract_date_from_headers,
    get_best_date,
    url_has_old_date,
    DateSource,
)


# ── ISO date parsing ─────────────────────────────────────────────


class TestParseIsoDate:
    def test_full_datetime_with_tz(self):
        assert _parse_iso_date("2025-03-15T10:30:00+00:00") is not None

    def test_full_datetime_z(self):
        dt = _parse_iso_date("2025-03-15T10:30:00Z")
        assert dt is not None
        assert dt.year == 2025

    def test_datetime_with_microseconds(self):
        dt = _parse_iso_date("2025-03-15T10:30:00.123456Z")
        assert dt is not None

    def test_date_only(self):
        dt = _parse_iso_date("2025-03-15")
        assert dt is not None
        assert dt.month == 3
        assert dt.day == 15

    def test_naive_datetime_gets_utc(self):
        dt = _parse_iso_date("2025-03-15T10:30:00")
        assert dt is not None
        assert dt.tzinfo == timezone.utc

    def test_empty_string(self):
        assert _parse_iso_date("") is None

    def test_garbage_input(self):
        assert _parse_iso_date("not-a-date") is None

    def test_whitespace_stripped(self):
        dt = _parse_iso_date("  2025-03-15  ")
        assert dt is not None


# ── RFC 2822 date parsing ────────────────────────────────────────


class TestParseRfc2822Date:
    def test_standard_rfc2822(self):
        dt = _parse_rfc2822_date("Sat, 15 Mar 2025 10:30:00 GMT")
        assert dt is not None
        assert dt.year == 2025

    def test_invalid_input(self):
        assert _parse_rfc2822_date("not a date") is None

    def test_empty_string(self):
        assert _parse_rfc2822_date("") is None


# ── URL date extraction ──────────────────────────────────────────


class TestExtractDateFromUrl:
    def test_slash_separated(self):
        result = extract_date_from_url("/news/2025/03/15/some-article")
        assert result is not None
        assert result.date.year == 2025
        assert result.date.month == 3
        assert result.date.day == 15
        assert result.source == DateSource.URL_PATTERN

    def test_hyphen_separated(self):
        result = extract_date_from_url("/news/2025-03-15-some-article")
        assert result is not None
        assert result.date.year == 2025

    def test_compact_format(self):
        result = extract_date_from_url("/news/20250315-some-article")
        assert result is not None
        assert result.date.year == 2025

    def test_no_date(self):
        assert extract_date_from_url("/news/some-article") is None

    def test_invalid_date(self):
        assert extract_date_from_url("/news/2025/13/40/bad") is None

    def test_year_out_of_range(self):
        assert extract_date_from_url("/news/1800/01/01/old") is None


# ── HTML date extraction ─────────────────────────────────────────


class TestExtractDatesFromHtml:
    def test_meta_tag(self):
        html = '<html><head><meta property="article:published_time" content="2025-03-15T10:00:00Z"></head><body></body></html>'
        results = extract_dates_from_html(html)
        assert len(results) >= 1
        assert results[0].source == DateSource.META_TAG

    def test_json_ld(self):
        html = '<html><head><script type="application/ld+json">{"@type":"Article","datePublished":"2025-03-15T10:00:00Z"}</script></head><body></body></html>'
        results = extract_dates_from_html(html)
        assert any(r.source == DateSource.JSON_LD for r in results)

    def test_json_ld_graph(self):
        html = '<html><head><script type="application/ld+json">{"@graph":[{"@type":"Article","datePublished":"2025-03-15T10:00:00Z"}]}</script></head><body></body></html>'
        results = extract_dates_from_html(html)
        assert any(r.source == DateSource.JSON_LD for r in results)

    def test_time_element(self):
        html = '<html><body><time datetime="2025-03-15T10:00:00Z">Mar 15</time></body></html>'
        results = extract_dates_from_html(html)
        assert any(r.source == DateSource.TIME_ELEMENT for r in results)

    def test_no_dates(self):
        html = '<html><body><p>No dates here</p></body></html>'
        results = extract_dates_from_html(html)
        assert results == []

    def test_malformed_json_ld_ignored(self):
        html = '<html><head><script type="application/ld+json">not valid json</script></head><body></body></html>'
        results = extract_dates_from_html(html)
        assert not any(r.source == DateSource.JSON_LD for r in results)


# ── HTTP header date extraction ──────────────────────────────────


class TestExtractDateFromHeaders:
    def test_last_modified(self):
        result = extract_date_from_headers({"Last-Modified": "Sat, 15 Mar 2025 10:30:00 GMT"})
        assert result is not None
        assert result.source == DateSource.HTTP_HEADER

    def test_lowercase_header(self):
        result = extract_date_from_headers({"last-modified": "Sat, 15 Mar 2025 10:30:00 GMT"})
        assert result is not None

    def test_no_header(self):
        assert extract_date_from_headers({}) is None

    def test_invalid_header(self):
        assert extract_date_from_headers({"Last-Modified": "not-a-date"}) is None


# ── Best date selection ──────────────────────────────────────────


class TestGetBestDate:
    def test_meta_beats_url(self):
        html = '<html><head><meta property="article:published_time" content="2025-06-01T10:00:00Z"></head><body></body></html>'
        result = get_best_date("/news/2025/03/15/slug", html)
        assert result is not None
        assert result.source == DateSource.META_TAG
        assert result.date.month == 6  # Meta date, not URL date

    def test_url_only(self):
        result = get_best_date("/news/2025/03/15/slug")
        assert result is not None
        assert result.source == DateSource.URL_PATTERN

    def test_no_signals(self):
        result = get_best_date("/about")
        assert result is None

    def test_headers_used(self):
        result = get_best_date("/page", headers={"Last-Modified": "Sat, 15 Mar 2025 10:30:00 GMT"})
        assert result is not None
        assert result.source == DateSource.HTTP_HEADER


# ── Pre-filter: URL has old date ─────────────────────────────────


class TestUrlHasOldDate:
    def test_old_date(self):
        cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert url_has_old_date("/news/2024/06/01/old-article", cutoff) is True

    def test_recent_date(self):
        cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert url_has_old_date("/news/2025/06/01/new-article", cutoff) is False

    def test_no_date_returns_false(self):
        cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert url_has_old_date("/news/some-article", cutoff) is False
