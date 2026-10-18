"""Tests for processing/discovery.py — sitemap, RSS, Atom parsing (pure functions, no network)."""

import pytest

from scrapepy.processing.discovery import (
    _parse_sitemap,
    _parse_rss,
    _parse_atom,
    _discover_feeds_from_html,
    parse_robots_sitemaps,
)


BASE_URL = "https://example.com"


# ── Sitemap XML parsing ─────────────────────────────────────────


class TestParseSitemap:
    def test_basic_urls(self):
        xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url>
                <loc>https://example.com/page1</loc>
                <lastmod>2025-03-15T10:00:00Z</lastmod>
            </url>
            <url>
                <loc>https://example.com/page2</loc>
                <lastmod>2025-03-16T10:00:00Z</lastmod>
            </url>
        </urlset>"""
        date_map, sub_sitemaps = _parse_sitemap(xml, BASE_URL)
        assert "/page1" in date_map
        assert "/page2" in date_map
        assert date_map["/page1"].year == 2025
        assert sub_sitemaps == []

    def test_sitemap_index(self):
        xml = """<?xml version="1.0"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <sitemap>
                <loc>https://example.com/sitemap-news.xml</loc>
            </sitemap>
            <sitemap>
                <loc>https://example.com/sitemap-posts.xml</loc>
            </sitemap>
        </sitemapindex>"""
        date_map, sub_sitemaps = _parse_sitemap(xml, BASE_URL)
        assert len(sub_sitemaps) == 2
        assert "https://example.com/sitemap-news.xml" in sub_sitemaps
        assert date_map == {}

    def test_url_without_lastmod(self):
        xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/no-date</loc></url>
        </urlset>"""
        date_map, _ = _parse_sitemap(xml, BASE_URL)
        assert "/no-date" not in date_map

    def test_external_urls_filtered(self):
        xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url>
                <loc>https://other-domain.com/page</loc>
                <lastmod>2025-03-15T10:00:00Z</lastmod>
            </url>
        </urlset>"""
        date_map, _ = _parse_sitemap(xml, BASE_URL)
        assert len(date_map) == 0

    def test_malformed_xml(self):
        date_map, sub_sitemaps = _parse_sitemap("this is not xml at all", BASE_URL)
        assert date_map == {}
        assert sub_sitemaps == []

    def test_empty_string(self):
        date_map, sub_sitemaps = _parse_sitemap("", BASE_URL)
        assert date_map == {}


# ── RSS feed parsing ────────────────────────────────────────────


class TestParseRss:
    def test_basic_items(self):
        xml = """<?xml version="1.0"?>
        <rss version="2.0">
            <channel>
                <item>
                    <link>https://example.com/article-1</link>
                    <pubDate>Sat, 15 Mar 2025 10:30:00 GMT</pubDate>
                </item>
                <item>
                    <link>https://example.com/article-2</link>
                    <pubDate>Sun, 16 Mar 2025 12:00:00 GMT</pubDate>
                </item>
            </channel>
        </rss>"""
        date_map = _parse_rss(xml, BASE_URL)
        assert "/article-1" in date_map
        assert "/article-2" in date_map
        assert date_map["/article-1"].year == 2025

    def test_item_without_date(self):
        xml = """<?xml version="1.0"?>
        <rss version="2.0">
            <channel>
                <item><link>https://example.com/no-date</link></item>
            </channel>
        </rss>"""
        date_map = _parse_rss(xml, BASE_URL)
        assert "/no-date" not in date_map

    def test_external_links_filtered(self):
        xml = """<?xml version="1.0"?>
        <rss version="2.0">
            <channel>
                <item>
                    <link>https://other.com/article</link>
                    <pubDate>Sat, 15 Mar 2025 10:30:00 GMT</pubDate>
                </item>
            </channel>
        </rss>"""
        date_map = _parse_rss(xml, BASE_URL)
        assert len(date_map) == 0

    def test_empty_feed(self):
        xml = """<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>"""
        assert _parse_rss(xml, BASE_URL) == {}

    def test_malformed_xml(self):
        assert _parse_rss("not xml", BASE_URL) == {}


# ── Atom feed parsing ───────────────────────────────────────────


class TestParseAtom:
    def test_basic_entries(self):
        xml = """<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <entry>
                <link rel="alternate" href="https://example.com/post-1"/>
                <published>2025-03-15T10:00:00Z</published>
            </entry>
        </feed>"""
        date_map = _parse_atom(xml, BASE_URL)
        assert "/post-1" in date_map

    def test_updated_fallback(self):
        xml = """<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <entry>
                <link href="https://example.com/post-2"/>
                <updated>2025-03-16T10:00:00Z</updated>
            </entry>
        </feed>"""
        date_map = _parse_atom(xml, BASE_URL)
        assert "/post-2" in date_map

    def test_relative_link(self):
        xml = """<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <entry>
                <link href="/post-3"/>
                <published>2025-03-17T10:00:00Z</published>
            </entry>
        </feed>"""
        date_map = _parse_atom(xml, BASE_URL)
        assert "/post-3" in date_map

    def test_empty_feed(self):
        xml = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>"""
        assert _parse_atom(xml, BASE_URL) == {}

    def test_malformed_xml(self):
        assert _parse_atom("garbage", BASE_URL) == {}


# ── Feed auto-discovery from HTML ───────────────────────────────


class TestDiscoverFeedsFromHtml:
    def test_rss_link(self):
        html = '<html><head><link rel="alternate" type="application/rss+xml" href="/feed"></head><body></body></html>'
        feeds = _discover_feeds_from_html(html, BASE_URL)
        assert len(feeds) == 1
        assert feeds[0] == "https://example.com/feed"

    def test_atom_link(self):
        html = '<html><head><link rel="alternate" type="application/atom+xml" href="/atom.xml"></head><body></body></html>'
        feeds = _discover_feeds_from_html(html, BASE_URL)
        assert len(feeds) == 1

    def test_no_feeds(self):
        html = '<html><head></head><body></body></html>'
        assert _discover_feeds_from_html(html, BASE_URL) == []

    def test_absolute_url(self):
        html = '<html><head><link rel="alternate" type="application/rss+xml" href="https://example.com/rss"></head><body></body></html>'
        feeds = _discover_feeds_from_html(html, BASE_URL)
        assert feeds[0] == "https://example.com/rss"

    def test_non_feed_alternate_skipped(self):
        html = '<html><head><link rel="alternate" type="text/html" href="/page"></head><body></body></html>'
        assert _discover_feeds_from_html(html, BASE_URL) == []


# ── Robots.txt sitemap parsing ──────────────────────────────────


class TestParseRobotsSitemaps:
    def test_single_sitemap(self):
        content = "Sitemap: https://example.com/sitemap.xml\nUser-agent: *\nDisallow:"
        sitemaps = parse_robots_sitemaps(content)
        assert sitemaps == ["https://example.com/sitemap.xml"]

    def test_multiple_sitemaps(self):
        content = "Sitemap: https://example.com/sitemap1.xml\nSitemap: https://example.com/sitemap2.xml"
        sitemaps = parse_robots_sitemaps(content)
        assert len(sitemaps) == 2

    def test_case_insensitive(self):
        content = "sitemap: https://example.com/sitemap.xml"
        sitemaps = parse_robots_sitemaps(content)
        assert len(sitemaps) == 1

    def test_empty_content(self):
        assert parse_robots_sitemaps("") == []

    def test_no_sitemaps(self):
        assert parse_robots_sitemaps("User-agent: *\nDisallow: /admin") == []

    def test_whitespace_handling(self):
        content = "  Sitemap:   https://example.com/sitemap.xml  "
        sitemaps = parse_robots_sitemaps(content)
        assert sitemaps == ["https://example.com/sitemap.xml"]
