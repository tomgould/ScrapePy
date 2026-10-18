"""Tests for output/writer.py — url_to_filepath numeric segment flattening."""

import os

import pytest
from scrapepy.config import ScrapeConfig
from scrapepy.output.writer import (
    _flatten_numeric_segments,
    article_filepath_in_section,
    article_relpath_in_section,
    extract_slug,
    section_index_filepath,
    url_to_filepath,
)


# ── Helper to build a minimal config ─────────────────────────────────

def _config(url="https://www.example.com", stay_in_path=False):
    return ScrapeConfig(url=url, stay_in_path=stay_in_path)


# ── _flatten_numeric_segments unit tests ─────────────────────────────


class TestFlattenNumericSegments:
    def test_standard_crash_net_pattern(self):
        """news/1090365/1/slug -> news/slug-1090365-1"""
        assert _flatten_numeric_segments("news/1090365/1/slug") == "news/slug-1090365-1"

    def test_no_numeric_segments(self):
        """about-us -> about-us (unchanged)"""
        assert _flatten_numeric_segments("about-us") == "about-us"

    def test_no_numeric_before_filename(self):
        """f1/news/slug -> f1/news/slug (unchanged)"""
        assert _flatten_numeric_segments("f1/news/slug") == "f1/news/slug"

    def test_trailing_numeric_no_slug(self):
        """f1/news/1005473 -> f1/news/1005473 (number IS the filename, nothing to flatten)"""
        assert _flatten_numeric_segments("f1/news/1005473") == "f1/news/1005473"

    def test_all_numeric(self):
        """123/456/789 -> 789-123-456"""
        assert _flatten_numeric_segments("123/456/789") == "789-123-456"

    def test_single_segment(self):
        """f1 -> f1 (unchanged, only one segment)"""
        assert _flatten_numeric_segments("f1") == "f1"

    def test_deep_numeric_chain(self):
        """a/1/2/3/4/slug -> a/slug-1-2-3-4"""
        assert _flatten_numeric_segments("a/1/2/3/4/slug") == "a/slug-1-2-3-4"

    def test_non_numeric_breaks_chain(self):
        """a/1/b/2/slug -> a/1/b/slug-2 (only consecutive numerics before filename)"""
        assert _flatten_numeric_segments("a/1/b/2/slug") == "a/1/b/slug-2"

    def test_single_numeric_before_slug(self):
        """section/12345/my-article -> section/my-article-12345"""
        assert _flatten_numeric_segments("section/12345/my-article") == "section/my-article-12345"

    def test_deeper_dirs_preserved(self):
        """motogp/news/1090365/1/marco-bezzecchi -> motogp/news/marco-bezzecchi-1090365-1"""
        result = _flatten_numeric_segments("motogp/news/1090365/1/marco-bezzecchi")
        assert result == "motogp/news/marco-bezzecchi-1090365-1"


# ── url_to_filepath integration tests ────────────────────────────────


class TestUrlToFilepath:
    def test_numeric_flattening(self):
        cfg = _config()
        result = url_to_filepath("/section/news/12345/1/slug", cfg)
        assert result.endswith(os.path.join("section", "news", "slug-12345-1.html"))

    def test_no_numeric_segments(self):
        cfg = _config()
        result = url_to_filepath("/about-us", cfg)
        assert result.endswith("about-us.html")

    def test_trailing_numeric_no_slug(self):
        cfg = _config()
        result = url_to_filepath("/f1/news/12345", cfg)
        assert result.endswith(os.path.join("f1", "news", "12345.html"))

    def test_single_segment(self):
        cfg = _config()
        result = url_to_filepath("/f1", cfg)
        assert result.endswith("f1.html")

    def test_root_path(self):
        cfg = _config()
        result = url_to_filepath("/", cfg)
        assert result.endswith("index.html")

    def test_existing_extension_preserved(self):
        cfg = _config()
        result = url_to_filepath("/file.xml", cfg)
        assert result.endswith("file.xml")

    def test_deep_numeric_chain(self):
        cfg = _config()
        result = url_to_filepath("/a/1/2/3/4/slug", cfg)
        assert result.endswith(os.path.join("a", "slug-1-2-3-4.html"))

    def test_query_and_fragment_stripped(self):
        cfg = _config()
        result = url_to_filepath("/section/123/slug?page=2#top", cfg)
        assert result.endswith(os.path.join("section", "slug-123.html"))

    def test_stay_in_path_prefix_stripping(self):
        cfg = _config(url="https://www.example.com/f1/news", stay_in_path=True)
        result = url_to_filepath("/f1/news/12345/1/slug", cfg)
        assert result.endswith("slug-12345-1.html")

    def test_extension_with_numeric_dirs(self):
        """Files with extensions still flatten numeric dirs."""
        cfg = _config()
        result = url_to_filepath("/docs/123/456/spec.json", cfg)
        assert result.endswith(os.path.join("docs", "spec-123-456.json"))

    def test_all_numeric_segments(self):
        cfg = _config()
        result = url_to_filepath("/123/456/789", cfg)
        assert result.endswith("789-123-456.html")


# ── extract_slug unit tests ──────────────────────────────────────────


class TestExtractSlug:
    def test_simple_slug(self):
        assert extract_slug("/sport/football/my-great-article") == "my-great-article"

    def test_numeric_id_suffix(self):
        assert extract_slug("/news/12345/my-article") == "my-article-12345"

    def test_bare_numeric(self):
        assert extract_slug("/news/12345") == "12345"

    def test_root_path(self):
        assert extract_slug("/") == "index"

    def test_extension_stripped(self):
        assert extract_slug("/sport/article.html") == "article"

    def test_query_fragment_stripped(self):
        assert extract_slug("/news/article?page=2#top") == "article"

    def test_deep_numeric_chain(self):
        assert extract_slug("/a/1/2/3/slug") == "slug-1-2-3"

    def test_empty_path(self):
        assert extract_slug("") == "index"


# ── article_filepath_in_section unit tests ───────────────────────────


class TestArticleRelpathInSection:
    """Tests for article_relpath_in_section — relative path computation."""

    def test_one_intermediate_dir(self):
        """Section /f1, article /f1/news/article-slug -> news/article-slug"""
        assert article_relpath_in_section("/f1/news/article-slug", "/f1") == "news/article-slug"

    def test_two_intermediate_dirs(self):
        """Section /sport, article /sport/football/great-match -> football/great-match"""
        assert article_relpath_in_section("/sport/football/great-match", "/sport") == "football/great-match"

    def test_no_intermediate_dirs(self):
        """Section /news, article /news/article-slug -> article-slug"""
        assert article_relpath_in_section("/news/article-slug", "/news") == "article-slug"

    def test_numeric_flattening(self):
        """Section /news, article /news/12345/1/my-story -> my-story-12345-1"""
        assert article_relpath_in_section("/news/12345/1/my-story", "/news") == "my-story-12345-1"

    def test_root_section(self):
        """Root section /, article /some-article -> some-article"""
        assert article_relpath_in_section("/some-article", "/") == "some-article"

    def test_deep_sub_path(self):
        """Section /sport, article /sport/f1/news/article -> f1/news/article"""
        assert article_relpath_in_section("/sport/f1/news/article", "/sport") == "f1/news/article"

    def test_html_extension_stripped(self):
        """HTML extension is stripped from slug."""
        assert article_relpath_in_section("/news/sub/article.html", "/news") == "sub/article"

    def test_numeric_in_intermediate_dir(self):
        """Numeric intermediate dirs between section and slug are flattened."""
        assert article_relpath_in_section("/f1/news/1090365/1/slug", "/f1") == "news/slug-1090365-1"

    def test_article_not_under_section(self):
        """Article not under section falls back to slug only."""
        assert article_relpath_in_section("/business/ftse-100-live", "/business/markets") == "ftse-100-live"

    def test_article_sibling_section(self):
        """Article from sibling section uses slug only."""
        assert article_relpath_in_section("/culture/art-of-london", "/culture/exhibitions") == "art-of-london"


class TestArticleFilepathInSection:
    def test_basic_preserves_intermediate(self):
        """Intermediate paths between section and article are preserved."""
        cfg = _config()
        result = article_filepath_in_section(
            url_path="/sport/football/my-article",
            section_path="/sport",
            config=cfg,
        )
        assert result.endswith(os.path.join("sport", "football", "my-article.md"))
        assert "MarkdownSites" in result

    def test_sub_section(self):
        cfg = _config()
        result = article_filepath_in_section(
            url_path="/sport/football/premier-league/great-match",
            section_path="/sport/football",
            config=cfg,
        )
        assert result.endswith(os.path.join("sport", "football", "premier-league", "great-match.md"))

    def test_numeric_flattening(self):
        cfg = _config()
        result = article_filepath_in_section(
            url_path="/news/12345/1/my-story",
            section_path="/news",
            config=cfg,
        )
        assert result.endswith(os.path.join("news", "my-story-12345-1.md"))

    def test_root_section(self):
        cfg = _config()
        result = article_filepath_in_section(
            url_path="/some-article",
            section_path="/",
            config=cfg,
        )
        assert result.endswith("some-article.md")
        assert "MarkdownSites" in result

    def test_html_extension(self):
        cfg = _config()
        result = article_filepath_in_section(
            url_path="/sport/article",
            section_path="/sport",
            config=cfg,
            ext=".html",
        )
        assert result.endswith(os.path.join("sport", "article.html"))

    def test_deep_path_preserved(self):
        """Article /f1/news/article under section /f1 preserves news/ dir."""
        cfg = _config()
        result = article_filepath_in_section(
            url_path="/f1/news/article-slug",
            section_path="/f1",
            config=cfg,
        )
        assert result.endswith(os.path.join("f1", "news", "article-slug.md"))


# ── section_index_filepath unit tests ─────────────────────────────


class TestSectionIndexFilepath:
    def test_nested_section(self):
        cfg = _config()
        result = section_index_filepath("/business/markets", cfg)
        assert result.endswith(os.path.join("business", "markets", "_index.md"))
        assert "MarkdownSites" in result

    def test_top_level_section(self):
        cfg = _config()
        result = section_index_filepath("/news", cfg)
        assert result.endswith(os.path.join("news", "_index.md"))
        assert "MarkdownSites" in result

    def test_root_section(self):
        cfg = _config()
        result = section_index_filepath("/", cfg)
        assert result.endswith("_index.md")
        assert "MarkdownSites" in result

    def test_deep_section(self):
        cfg = _config()
        result = section_index_filepath("/sport/football/premier-league", cfg)
        assert result.endswith(
            os.path.join("sport", "football", "premier-league", "_index.md")
        )
