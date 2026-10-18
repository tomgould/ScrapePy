"""Tests for processing/classifier.py — page type classification."""

import pytest

from scrapepy.processing.classifier import (
    PageType,
    is_section_url,
    classify_page,
    _compute_link_density,
)


# ── Section URL detection ───────────────────────────────────────


class TestIsSectionUrl:
    @pytest.mark.parametrize("path", [
        "/",
        "/index.html",
        "/category/tech",
        "/tag/python",
        "/topic/ai",
        "/archive/2025",
        "/author/jane",
        "/page/2",
        "/news",
        "/blog",
        "/sport",
        "/news/opinion",
        "/feed",
        "/rss",
    ])
    def test_section_paths(self, path):
        assert is_section_url(path) is True

    @pytest.mark.parametrize("path", [
        "/news/2025/03/15/my-article-slug",
        "/news/very-long-article-about-something-interesting",
        "/api/v2/users",
    ])
    def test_non_section_paths(self, path):
        assert is_section_url(path) is False


# ── Link density calculation ────────────────────────────────────


class TestComputeLinkDensity:
    def test_all_links(self):
        # Need > 50 chars of text to pass the short-text threshold
        links = " ".join(f'<a href="/{i}">Link number {i} here</a>' for i in range(10))
        html = f'<html><body>{links}</body></html>'
        density = _compute_link_density(html)
        assert density > 0.8

    def test_no_links(self):
        html = '<html><body><p>This is a paragraph with no links at all, just regular text content that goes on for a while.</p></body></html>'
        density = _compute_link_density(html)
        assert density == 0.0

    def test_mixed_content(self):
        html = '<html><body><p>This is some regular text content that is long enough to pass the fifty character threshold check.</p><a href="/link">A link</a></body></html>'
        density = _compute_link_density(html)
        assert 0.0 < density < 0.5

    def test_empty_body(self):
        html = '<html><body></body></html>'
        assert _compute_link_density(html) == 0.0

    def test_no_body(self):
        html = '<html><head><title>Test</title></head></html>'
        assert _compute_link_density(html) == 0.0

    def test_nav_excluded(self):
        # Nav links should be stripped before density calculation
        html = '<html><body><nav><a href="/1">Nav Link</a></nav><p>Regular content that is long enough to count.</p></body></html>'
        density = _compute_link_density(html)
        assert density == 0.0

    def test_short_text_returns_zero(self):
        html = '<html><body>Hi</body></html>'
        assert _compute_link_density(html) == 0.0


# ── Full page classification ────────────────────────────────────


class TestClassifyPage:
    def test_homepage(self):
        assert classify_page("/", "<html><body></body></html>") == PageType.SECTION

    def test_category_page(self):
        assert classify_page("/category/tech", "<html><body></body></html>") == PageType.SECTION

    def test_article_with_date_url(self):
        assert classify_page(
            "/news/2025/03/15/breaking-news",
            "<html><body><p>Article content</p></body></html>",
        ) == PageType.ARTICLE

    def test_article_with_hyphenated_date(self):
        assert classify_page(
            "/news/2025-03-15-breaking-news",
            "<html><body></body></html>",
        ) == PageType.ARTICLE

    def test_article_with_compact_date(self):
        assert classify_page(
            "/news/20250315-breaking-news",
            "<html><body></body></html>",
        ) == PageType.ARTICLE

    def test_unknown_page(self):
        html = '<html><body><p>Just some content without dates or lots of links, just a regular page with enough text.</p></body></html>'
        assert classify_page("/about-us-extended-page-section", html) == PageType.UNKNOWN

    def test_high_link_density_no_date_is_section(self):
        links = " ".join(f'<a href="/page/{i}">Link {i}</a>' for i in range(50))
        html = f'<html><body>{links}</body></html>'
        assert classify_page("/some-deep-path/sub-section-level", html) == PageType.SECTION

    def test_meta_date_makes_article(self):
        html = '''<html><head>
            <meta property="article:published_time" content="2025-03-15T10:00:00Z">
        </head><body><p>Article content that is long enough to avoid short text threshold in link density calculation.</p></body></html>'''
        assert classify_page("/some-path-without-obvious-date/long-enough-slug", html) == PageType.ARTICLE

    def test_section_roots(self):
        # Short single-segment paths are classified as sections
        for path in ["/sport", "/business", "/tech"]:
            assert classify_page(path, "<html><body></body></html>") == PageType.SECTION

    def test_two_segment_section(self):
        assert classify_page("/news/opinion", "<html><body></body></html>") == PageType.SECTION
