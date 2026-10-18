"""Tests for processing/urls.py — URL normalization, scope, classification."""

import pytest

from scrapepy.processing.urls import (
    is_in_scope,
    normalize_link,
    extract_links_from_html,
    has_article_slug,
    is_likely_article_path,
    discover_section_pages,
    classify_asset_url,
    NON_CONTENT_PATHS,
    SKIP_EXTENSIONS,
    _NON_SECTION_PATHS,
)
from scrapepy.config import ScrapeConfig


def _make_config(url="https://example.com", stay_in_path=False, path_wildcard=False, **kw):
    return ScrapeConfig(url=url, stay_in_path=stay_in_path, path_wildcard=path_wildcard, **kw)


# ── Scope checking ──────────────────────────────────────────────


class TestIsInScope:
    def test_no_stay_in_path(self):
        config = _make_config()
        assert is_in_scope("/any/path", config) is True

    def test_stay_in_path_exact(self):
        config = _make_config("https://example.com/docs", stay_in_path=True)
        assert is_in_scope("/docs", config) is True

    def test_stay_in_path_child(self):
        config = _make_config("https://example.com/docs", stay_in_path=True)
        assert is_in_scope("/docs/getting-started", config) is True

    def test_stay_in_path_outside(self):
        config = _make_config("https://example.com/docs", stay_in_path=True)
        assert is_in_scope("/blog/post", config) is False

    def test_stay_in_path_partial_prefix(self):
        config = _make_config("https://example.com/doc", stay_in_path=True)
        # /docs should NOT match /doc (without trailing /)
        assert is_in_scope("/docs", config) is False

    def test_wildcard_mode(self):
        config = _make_config("https://example.com/v2", stay_in_path=True, path_wildcard=True)
        assert is_in_scope("/api/v2/users", config) is True
        assert is_in_scope("/api/v1/users", config) is False

    def test_strips_query_and_fragment(self):
        config = _make_config("https://example.com/docs", stay_in_path=True)
        assert is_in_scope("/docs/page?q=test#section", config) is True


# ── Link normalization ──────────────────────────────────────────


class TestNormalizeLink:
    def test_absolute_path(self):
        assert normalize_link("/page", "/", "https://example.com") == "/page"

    def test_relative_link(self):
        result = normalize_link("child", "/parent/current", "https://example.com")
        assert result == "/parent/child"

    def test_full_url_same_domain(self):
        result = normalize_link("https://example.com/page", "/", "https://example.com")
        assert result == "/page"

    def test_external_link(self):
        assert normalize_link("https://other.com/page", "/", "https://example.com") is None

    def test_skip_extensions(self):
        assert normalize_link("/file.pdf", "/", "https://example.com") is None
        assert normalize_link("/image.jpg", "/", "https://example.com") is None
        assert normalize_link("/style.css", "/", "https://example.com") is None

    def test_mailto(self):
        assert normalize_link("mailto:test@example.com", "/", "https://example.com") is None

    def test_javascript(self):
        assert normalize_link("javascript:void(0)", "/", "https://example.com") is None

    def test_empty_link(self):
        assert normalize_link("", "/", "https://example.com") is None

    def test_query_stripped(self):
        assert normalize_link("/page?sort=date", "/", "https://example.com") == "/page"

    def test_fragment_stripped(self):
        assert normalize_link("/page#section", "/", "https://example.com") == "/page"

    def test_dot_dot_normalized(self):
        result = normalize_link("../sibling", "/a/b/c", "https://example.com")
        assert result == "/a/sibling"

    def test_dot_slash_normalized(self):
        result = normalize_link("./page", "/dir/current", "https://example.com")
        assert result == "/dir/page"

    def test_data_uri(self):
        assert normalize_link("data:image/png;base64,abc", "/", "https://example.com") is None


# ── HTML link extraction ────────────────────────────────────────


class TestExtractLinksFromHtml:
    def test_href(self):
        html = '<a href="/page">Link</a>'
        links = extract_links_from_html(html, "https://example.com")
        assert "/page" in links

    def test_data_href(self):
        html = '<div data-href="/dynamic">Content</div>'
        links = extract_links_from_html(html, "https://example.com")
        assert "/dynamic" in links

    def test_data_url(self):
        html = '<div data-url="/endpoint">Content</div>'
        links = extract_links_from_html(html, "https://example.com")
        assert "/endpoint" in links

    def test_data_link(self):
        html = '<div data-link="/target">Content</div>'
        links = extract_links_from_html(html, "https://example.com")
        assert "/target" in links

    def test_deduplicates(self):
        html = '<a href="/page">One</a><a href="/page">Two</a>'
        links = extract_links_from_html(html, "https://example.com")
        assert links.count("/page") == 1

    def test_empty_html(self):
        assert extract_links_from_html("", "https://example.com") == []


# ── Article slug detection ──────────────────────────────────────


class TestHasArticleSlug:
    def test_long_slug(self):
        assert has_article_slug("/news/my-long-article-title") is True

    def test_multiple_hyphens(self):
        assert has_article_slug("/news/car-review-2025") is True

    def test_simple_segment(self):
        assert has_article_slug("/news/reviews") is False

    def test_numeric_segment(self):
        assert has_article_slug("/news/10757119") is False

    def test_empty(self):
        assert has_article_slug("") is False

    def test_trailing_slash(self):
        assert has_article_slug("/news/my-long-article-title/") is True

    def test_short_with_one_hyphen(self):
        assert has_article_slug("/news/my-page") is False


# ── Likely article path detection ───────────────────────────────


class TestIsLikelyArticlePath:
    def test_three_hyphens(self):
        assert is_likely_article_path("/news/this-is-an-article") is True

    def test_long_slug_with_hyphens(self):
        assert is_likely_article_path("/news/very-long-article-slug-that-is-descriptive") is True

    def test_cms_id_suffix(self):
        assert is_likely_article_path("/news/story-12345") is True

    def test_section_name(self):
        # Only 2 hyphens and short — not article
        assert is_likely_article_path("/arts-and-entertainment") is False

    def test_empty(self):
        assert is_likely_article_path("") is False

    def test_plain_segment(self):
        assert is_likely_article_path("/reviews") is False


# ── Section page discovery ──────────────────────────────────────


class TestDiscoverSectionPages:
    def test_basic(self):
        html = '<a href="/news">News</a><a href="/sport">Sport</a>'
        sections = discover_section_pages(html, "https://example.com")
        assert "/news" in sections
        assert "/sport" in sections

    def test_filters_articles(self):
        html = '<a href="/news/this-is-a-long-article-headline">Article</a>'
        sections = discover_section_pages(html, "https://example.com")
        assert sections == []

    def test_filters_non_section_paths(self):
        html = '<a href="/author/john">Author</a><a href="/tag/python">Tag</a>'
        sections = discover_section_pages(html, "https://example.com")
        assert sections == []

    def test_filters_external(self):
        html = '<a href="https://other.com/page">External</a>'
        sections = discover_section_pages(html, "https://example.com")
        assert sections == []

    def test_sorted_output(self):
        html = '<a href="/z-section">Z</a><a href="/a-section">A</a>'
        sections = discover_section_pages(html, "https://example.com")
        assert sections == sorted(sections)

    def test_deduplicates(self):
        html = '<a href="/news">News</a><a href="/news">News Again</a>'
        sections = discover_section_pages(html, "https://example.com")
        assert sections.count("/news") == 1

    def test_strips_query_and_fragment(self):
        html = '<a href="/news?page=2#top">News</a>'
        sections = discover_section_pages(html, "https://example.com")
        assert "/news" in sections

    def test_filters_rss(self):
        html = '<a href="/feed">Feed</a><a href="/rss">RSS</a>'
        sections = discover_section_pages(html, "https://example.com")
        assert sections == []

    def test_filters_rss_compound(self):
        html = '<a href="/rssnewsfeeds">RSS Feeds</a><a href="/rssfeeds">Feeds</a>'
        sections = discover_section_pages(html, "https://example.com")
        assert sections == []


# ── Asset URL classification ────────────────────────────────────


class TestClassifyAssetUrl:
    def test_image(self):
        result = classify_asset_url("/img/photo.jpg", "https://example.com", False)
        assert result is not None
        assert result["folder"] == "images"
        assert result["filename"] == "photo.jpg"

    def test_css_without_include(self):
        result = classify_asset_url("/style.css", "https://example.com", False)
        assert result is None

    def test_css_with_include(self):
        result = classify_asset_url("/style.css", "https://example.com", True)
        assert result is not None
        assert result["folder"] == "css"

    def test_js_without_include(self):
        result = classify_asset_url("/app.js", "https://example.com", False)
        assert result is None

    def test_js_with_include(self):
        result = classify_asset_url("/app.js", "https://example.com", True)
        assert result is not None
        assert result["folder"] == "js"

    def test_font(self):
        result = classify_asset_url("/fonts/roboto.woff2", "https://example.com", True)
        assert result is not None
        assert result["folder"] == "fonts"

    def test_media(self):
        result = classify_asset_url("/doc/report.pdf", "https://example.com", False)
        assert result is not None
        assert result["folder"] == "media"

    def test_data_uri_skipped(self):
        assert classify_asset_url("data:image/png;base64,abc", "https://example.com", True) is None

    def test_javascript_uri_skipped(self):
        assert classify_asset_url("javascript:void(0)", "https://example.com", True) is None

    def test_protocol_relative(self):
        result = classify_asset_url("//cdn.example.com/img/photo.jpg", "https://example.com", False)
        assert result is not None
        assert result["full_url"].startswith("https:")

    def test_relative_url(self):
        result = classify_asset_url("/images/photo.png", "https://example.com", False)
        assert result is not None
        assert "example.com" in result["full_url"]

    def test_long_filename_truncated(self):
        long_name = "a" * 150 + ".jpg"
        result = classify_asset_url(f"/images/{long_name}", "https://example.com", False)
        assert result is not None
        assert len(result["filename"]) <= 100


# ── NON_CONTENT_PATHS regex ─────────────────────────────────────


class TestNonContentPaths:
    @pytest.mark.parametrize("path", [
        "/login", "/sign-in", "/signup", "/register", "/logout",
        "/settings", "/account", "/admin", "/wp-admin",
        "/cart", "/checkout", "/share", "/embed",
    ])
    def test_matches(self, path):
        assert NON_CONTENT_PATHS.search(path) is not None

    @pytest.mark.parametrize("path", [
        "/news", "/sport", "/blog/post", "/about-us",
    ])
    def test_no_match(self, path):
        assert NON_CONTENT_PATHS.search(path) is None


# ── Language variant filtering ────────────────────────────────────


class TestLanguageVariantFiltering:
    """Language codes are blocked as sections, but short non-language paths are not."""

    @pytest.mark.parametrize("path", [
        "/id", "/id/", "/id/news",
        "/es", "/fr", "/de", "/pt", "/it",
        "/ja", "/ko", "/zh", "/ar", "/ru",
        "/en", "/nl", "/sv", "/pl", "/tr",
    ])
    def test_language_codes_blocked(self, path):
        assert _NON_SECTION_PATHS.search(path) is not None, f"{path} should be blocked"

    @pytest.mark.parametrize("path", [
        "/en-us", "/en-US", "/pt-br", "/zh-cn", "/fr-ca",
        "/en_GB", "/pt_BR", "/zh-hans", "/zh-hant",
    ])
    def test_locale_patterns_blocked(self, path):
        assert _NON_SECTION_PATHS.search(path) is not None, f"{path} should be blocked"

    @pytest.mark.parametrize("path", [
        "/f1", "/rr", "/ww", "/sb", "/gp",
        "/f1/news", "/rr/results",
        "/le-mans", "/le-mans/news", "/go-kart",
    ])
    def test_non_language_two_letter_paths_allowed(self, path):
        assert _NON_SECTION_PATHS.search(path) is None, f"{path} should NOT be blocked"

    def test_language_in_discover_section_pages(self):
        """discover_section_pages filters out language variant paths."""
        html = '<a href="/id">Indonesian</a><a href="/f1">F1</a><a href="/news">News</a>'
        sections = discover_section_pages(html, "https://example.com")
        assert "/id" not in sections
        assert "/f1" in sections
        assert "/news" in sections


# ── SKIP_EXTENSIONS regex ───────────────────────────────────────


class TestSkipExtensions:
    @pytest.mark.parametrize("path", [
        "/file.pdf", "/image.jpg", "/style.css", "/app.js",
        "/font.woff2", "/video.mp4",
    ])
    def test_matches(self, path):
        assert SKIP_EXTENSIONS.search(path) is not None

    @pytest.mark.parametrize("path", [
        "/page", "/page.html", "/page.htm", "/page.php",
    ])
    def test_no_match(self, path):
        assert SKIP_EXTENSIONS.search(path) is None
