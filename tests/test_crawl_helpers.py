"""Tests for crawl.py helper functions — article detection, section splitting."""

import pytest
from scrapepy.config import ScrapeConfig
from scrapepy.processing.urls import has_article_slug
from scrapepy.modes.crawl import (
    _looks_like_article_path,
    _split_section_links,
    _normalize_link_to_path,
)


# ── Article slug detection ─────────────────────────────────────────


class TestHasArticleSlug:
    def test_long_hyphenated_slug(self):
        assert has_article_slug("/news/garmin-catalyst-2-track-driving-coach") is True

    def test_multiple_hyphens(self):
        assert has_article_slug("/news/max-verstappen-wins") is True

    def test_short_section_name(self):
        assert has_article_slug("/news") is False
        assert has_article_slug("/reviews") is False

    def test_numeric_id_only(self):
        assert has_article_slug("/news/10757119") is False

    def test_empty_path(self):
        assert has_article_slug("") is False
        assert has_article_slug("/") is False

    def test_path_with_trailing_slash(self):
        assert has_article_slug("/news/long-article-title-here/") is True

    def test_short_slug_with_two_hyphens(self):
        """Two hyphens even in short slug is article-like."""
        assert has_article_slug("/news/a-b-c") is True


# ── Article path detection ─────────────────────────────────────────


class TestLooksLikeArticlePath:
    def test_child_with_slug(self):
        assert _looks_like_article_path("/f1/news/verstappen-wins-race", "/f1") is True

    def test_not_a_child(self):
        assert _looks_like_article_path("/motogp/article-title-here", "/f1") is False

    def test_child_but_short_segment(self):
        assert _looks_like_article_path("/f1/news", "/f1") is False

    def test_section_itself(self):
        assert _looks_like_article_path("/f1", "/f1") is False


# ── Section link splitting ─────────────────────────────────────────


class TestSplitSectionLinks:
    def setup_method(self):
        self.config = ScrapeConfig(url="https://example.com")

    def test_splits_articles_from_nav(self):
        links = [
            "/f1/news",
            "/f1/news/long-article-title-about-racing",
            "/f1/news/another-big-article-slug-here",
            "/f1/results",
        ]
        articles, other = _split_section_links(links, "/f1", self.config, set())
        assert len(articles) == 2
        assert articles[0] == "/f1/news/long-article-title-about-racing"
        assert articles[1] == "/f1/news/another-big-article-slug-here"

    def test_visited_urls_excluded(self):
        links = ["/f1/news/already-visited-article-slug"]
        visited = {"/f1/news/already-visited-article-slug"}
        articles, other = _split_section_links(links, "/f1", self.config, visited)
        assert len(articles) == 0

    def test_external_links_to_other(self):
        links = [
            "https://other.com/page",
            "/f1/news/real-article-title-here",
        ]
        articles, other = _split_section_links(links, "/f1", self.config, set())
        assert len(articles) == 1
        assert any("other.com" in l for l in other)

    def test_preserves_dom_order(self):
        """Article links maintain their DOM order."""
        links = [
            "/section/third-article-slug-here",
            "/section/first-article-slug-here",
            "/section/second-article-slug-here",
        ]
        articles, _ = _split_section_links(links, "/section", self.config, set())
        assert articles == [
            "/section/third-article-slug-here",
            "/section/first-article-slug-here",
            "/section/second-article-slug-here",
        ]


# ── max_articles config ────────────────────────────────────────────


class TestMaxArticlesConfig:
    def test_default_is_zero(self):
        config = ScrapeConfig(url="https://example.com")
        assert config.max_articles == 0

    def test_set_via_constructor(self):
        config = ScrapeConfig(url="https://example.com", max_articles=10)
        assert config.max_articles == 10

    def test_min_and_max_coexist(self):
        config = ScrapeConfig(
            url="https://example.com",
            min_articles=3,
            max_articles=10,
            recent="1w",
        )
        assert config.min_articles == 3
        assert config.max_articles == 10
        assert config.has_date_filter is True


# ── Thumbnail extraction ──────────────────────────────────────────


class TestExtractArticleThumbnails:
    def test_extract_thumbnail_from_section_html(self):
        from scrapepy.modes.crawl import _extract_article_thumbnails
        html = '''
        <div class="article-card">
          <a href="/sport/my-article">
            <img src="/images/thumb1.jpg" alt="Article 1">
            <h3>My Article</h3>
          </a>
        </div>
        <div class="article-card">
          <a href="/sport/another-article">
            <img src="/images/thumb2.jpg">
            <h3>Another Article</h3>
          </a>
        </div>
        '''
        thumbnails = _extract_article_thumbnails(html, ["/sport/my-article", "/sport/another-article"])
        assert thumbnails.get("/sport/my-article") == "/images/thumb1.jpg"
        assert thumbnails.get("/sport/another-article") == "/images/thumb2.jpg"

    def test_extract_thumbnail_no_match(self):
        from scrapepy.modes.crawl import _extract_article_thumbnails
        html = '<div><a href="/other"><img src="/img.jpg"></a></div>'
        thumbnails = _extract_article_thumbnails(html, ["/sport/article"])
        assert len(thumbnails) == 0

    def test_extract_thumbnail_parent_img(self):
        """Image is sibling of <a>, not inside it."""
        from scrapepy.modes.crawl import _extract_article_thumbnails
        html = '''
        <div class="card">
          <img src="/images/thumb.jpg">
          <a href="/news/story">Story</a>
        </div>
        '''
        thumbnails = _extract_article_thumbnails(html, ["/news/story"])
        assert thumbnails.get("/news/story") == "/images/thumb.jpg"
