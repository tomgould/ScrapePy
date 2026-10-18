"""Tests for scrapepy.processing.discovery, robots.txt parsing."""

import pytest
from scrapepy.processing.discovery import parse_robots_sitemaps


class TestParseRobotsSitemaps:
    def test_single_sitemap(self):
        content = "User-agent: *\nDisallow: /admin\nSitemap: https://example.com/sitemap.xml\n"
        result = parse_robots_sitemaps(content)
        assert result == ["https://example.com/sitemap.xml"]

    def test_multiple_sitemaps(self):
        content = (
            "User-agent: *\n"
            "Sitemap: https://example.com/sitemap.xml\n"
            "Sitemap: https://example.com/sitemap-news.xml\n"
            "Sitemap: https://example.com/sitemap-images.xml\n"
        )
        result = parse_robots_sitemaps(content)
        assert len(result) == 3
        assert result[1] == "https://example.com/sitemap-news.xml"

    def test_case_insensitive(self):
        content = "sitemap: https://example.com/sitemap.xml\nSITEMAP: https://example.com/other.xml\n"
        result = parse_robots_sitemaps(content)
        assert len(result) == 2

    def test_empty_content(self):
        assert parse_robots_sitemaps("") == []

    def test_no_sitemaps(self):
        content = "User-agent: *\nDisallow: /admin\nAllow: /\n"
        assert parse_robots_sitemaps(content) == []

    def test_whitespace_handling(self):
        content = "Sitemap:   https://example.com/sitemap.xml   \n"
        result = parse_robots_sitemaps(content)
        assert result == ["https://example.com/sitemap.xml"]

    def test_mixed_directives(self):
        """Sitemap directives mixed with other directives are all found."""
        content = (
            "User-agent: Googlebot\n"
            "Disallow: /tmp\n"
            "Sitemap: https://example.com/sitemap.xml\n"
            "User-agent: *\n"
            "Allow: /\n"
            "Sitemap: https://example.com/news-sitemap.xml\n"
        )
        result = parse_robots_sitemaps(content)
        assert len(result) == 2

    def test_empty_sitemap_value_skipped(self):
        """Sitemap: with no URL value is skipped."""
        content = "Sitemap:\nSitemap: https://example.com/real.xml\n"
        result = parse_robots_sitemaps(content)
        assert result == ["https://example.com/real.xml"]
