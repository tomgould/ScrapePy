"""Tests for YAML frontmatter generation in markdown output."""

import os
import tempfile
from pathlib import Path

import pytest

from scrapepy.convert.to_markdown import HTMLToMarkdown


class TestBuildFrontmatter:
    """Tests for _build_frontmatter() static method."""

    def test_all_fields(self):
        metadata = {
            "title": "Test Article",
            "url": "https://example.com/news/test-article",
            "date": "2026-02-20",
            "date_source": "META_TAG",
            "page_type": "article",
            "word_count": 1247,
            "description": "A test article about testing",
            "domain": "example.com",
        }
        result = HTMLToMarkdown._build_frontmatter(metadata)
        assert result.startswith("---\n")
        assert result.endswith("---\n")
        assert "title: Test Article\n" in result
        assert 'url: "https://example.com/news/test-article"\n' in result
        assert "date: 2026-02-20\n" in result
        assert "date_source: META_TAG\n" in result
        assert "page_type: article\n" in result
        assert "word_count: 1247\n" in result
        assert "description: A test article about testing\n" in result
        assert "domain: example.com\n" in result

    def test_minimal_fields(self):
        metadata = {"domain": "example.com"}
        result = HTMLToMarkdown._build_frontmatter(metadata)
        # Core fields (date, date_source, page_type) always present as null
        assert "date: null\n" in result
        assert "date_source: null\n" in result
        assert "page_type: null\n" in result
        assert "domain: example.com\n" in result

    def test_empty_metadata(self):
        result = HTMLToMarkdown._build_frontmatter({})
        # Core fields always present even with empty metadata
        assert "date: null\n" in result
        assert "date_source: null\n" in result
        assert "page_type: null\n" in result

    def test_none_values_excluded_except_core(self):
        metadata = {"title": "Test", "date": None, "domain": "example.com"}
        result = HTMLToMarkdown._build_frontmatter(metadata)
        # date is a core field — shown as null even when None
        assert "date: null\n" in result
        assert "title: Test\n" in result
        assert "domain: example.com\n" in result
        # Non-core fields with None are still excluded
        assert "description:" not in result

    def test_yaml_special_chars_quoted(self):
        metadata = {"title": 'Article: "The Best" #1'}
        result = HTMLToMarkdown._build_frontmatter(metadata)
        assert 'title: "Article: \\"The Best\\" #1"' in result

    def test_colon_in_value_quoted(self):
        metadata = {"description": "Summary: this is a test"}
        result = HTMLToMarkdown._build_frontmatter(metadata)
        assert 'description: "Summary: this is a test"' in result

    def test_field_order(self):
        metadata = {
            "domain": "example.com",
            "title": "Test",
            "word_count": 100,
            "url": "https://example.com/test",
        }
        result = HTMLToMarkdown._build_frontmatter(metadata)
        lines = result.strip().split("\n")
        # Filter to only field lines (not --- markers)
        fields = [l.split(":")[0] for l in lines if not l.startswith("---")]
        # Core fields (date, date_source, page_type) included as null
        assert fields == ["title", "url", "date", "date_source", "page_type", "word_count", "domain"]

    def test_word_count_is_integer(self):
        metadata = {"word_count": 500}
        result = HTMLToMarkdown._build_frontmatter(metadata)
        assert "word_count: 500\n" in result
        assert '"500"' not in result


class TestComputeWordCount:
    """Tests for _compute_word_count() static method."""

    def test_plain_text(self):
        assert HTMLToMarkdown._compute_word_count("hello world foo bar") == 4

    def test_markdown_headings(self):
        md = "# Title\n\nSome body text here."
        count = HTMLToMarkdown._compute_word_count(md)
        assert count == 5  # Title Some body text here.

    def test_markdown_links(self):
        md = "Click [here](https://example.com) for more [info](https://example.com/info)."
        count = HTMLToMarkdown._compute_word_count(md)
        assert count == 5  # Click here for more info.

    def test_bold_italic(self):
        md = "This is **bold** and *italic* text."
        count = HTMLToMarkdown._compute_word_count(md)
        assert count == 6

    def test_empty_string(self):
        assert HTMLToMarkdown._compute_word_count("") == 0

    def test_images_stripped(self):
        md = "Text before ![alt text](image.png) text after."
        count = HTMLToMarkdown._compute_word_count(md)
        assert count == 6  # Text before alt text text after.

    def test_frontmatter_excluded(self):
        md = "---\ntitle: Test\nword_count: 5\n---\n\n# Title\n\nBody text here."
        count = HTMLToMarkdown._compute_word_count(md)
        # Should not count frontmatter fields as words
        assert count == 4  # Title Body text here.


class TestExtractDescription:
    """Tests for _extract_description() static method."""

    def test_meta_name_description(self):
        html = '<html><head><meta name="description" content="Test description here"></head><body></body></html>'
        assert HTMLToMarkdown._extract_description(html) == "Test description here"

    def test_og_description(self):
        html = '<html><head><meta property="og:description" content="OG description"></head><body></body></html>'
        assert HTMLToMarkdown._extract_description(html) == "OG description"

    def test_meta_name_preferred_over_og(self):
        html = (
            '<html><head>'
            '<meta name="description" content="Name desc">'
            '<meta property="og:description" content="OG desc">'
            '</head><body></body></html>'
        )
        assert HTMLToMarkdown._extract_description(html) == "Name desc"

    def test_missing_description(self):
        html = "<html><head><title>Test</title></head><body></body></html>"
        assert HTMLToMarkdown._extract_description(html) is None

    def test_empty_description(self):
        html = '<html><head><meta name="description" content=""></head><body></body></html>'
        assert HTMLToMarkdown._extract_description(html) is None


class TestConvertFileWithFrontmatter:
    """Integration tests for convert_file() with frontmatter."""

    def _make_html(self, title="Test Page", body="This is test content with enough words to pass the length check. " * 5,
                   description=None, date_meta=None):
        parts = [f"<html><head><title>{title}</title>"]
        if description:
            parts.append(f'<meta name="description" content="{description}">')
        if date_meta:
            parts.append(f'<meta property="article:published_time" content="{date_meta}">')
        parts.append(f"</head><body><article><h1>{title}</h1><p>{body}</p></article></body></html>")
        return "".join(parts)

    def test_frontmatter_with_crawl_metadata(self):
        """Crawl mode passes metadata dict to convert_file()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            input_dir = os.path.join(tmpdir, "input")
            os.makedirs(input_dir)
            filepath = os.path.join(input_dir, "test-article.html")
            html = self._make_html(description="A test page")
            with open(filepath, "w") as f:
                f.write(html)

            converter = HTMLToMarkdown(input_dir)
            metadata = {
                "url": "https://example.com/test-article",
                "domain": "example.com",
                "page_type": "article",
                "date": "2026-02-20",
                "date_source": "META_TAG",
            }
            converter.convert_file(Path(filepath), metadata=metadata)

            md_path = converter.output_dir / "test-article.md"
            assert md_path.exists()
            content = md_path.read_text()
            assert content.startswith("---\n")
            assert 'url: "https://example.com/test-article"\n' in content
            assert "domain: example.com\n" in content
            assert "page_type: article\n" in content
            assert "date: 2026-02-20\n" in content
            assert "word_count:" in content
            assert "# Test Page" in content

    def test_frontmatter_batch_mode_extracts_metadata(self):
        """Batch mode (no metadata passed) extracts from HTML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            input_dir = os.path.join(tmpdir, "input")
            os.makedirs(input_dir)
            filepath = os.path.join(input_dir, "about.html")
            html = self._make_html(title="About Us", description="About our company")
            with open(filepath, "w") as f:
                f.write(html)

            converter = HTMLToMarkdown(
                input_dir,
                base_url="https://example.com",
                domain="example.com",
            )
            converter.convert_file(Path(filepath))

            md_path = converter.output_dir / "about.md"
            assert md_path.exists()
            content = md_path.read_text()
            assert content.startswith("---\n")
            assert "domain: example.com\n" in content
            assert "word_count:" in content
            assert "title: About Us\n" in content

    def test_skipped_files_no_frontmatter(self):
        """Skipped files (error pages, XML) should not produce output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            input_dir = os.path.join(tmpdir, "input")
            os.makedirs(input_dir)
            filepath = os.path.join(input_dir, "error.html")
            with open(filepath, "w") as f:
                f.write("<html><head><title>404 Not Found</title></head><body><h1>404</h1></body></html>")

            converter = HTMLToMarkdown(input_dir)
            result = converter.convert_file(Path(filepath))
            assert result is False

    def test_title_captured_from_heading(self):
        """Title should be captured from the first # heading."""
        with tempfile.TemporaryDirectory() as tmpdir:
            input_dir = os.path.join(tmpdir, "input")
            os.makedirs(input_dir)
            filepath = os.path.join(input_dir, "article.html")
            html = self._make_html(title="My Great Article")
            with open(filepath, "w") as f:
                f.write(html)

            converter = HTMLToMarkdown(input_dir)
            metadata = {"url": "https://example.com/article", "domain": "example.com"}
            converter.convert_file(Path(filepath), metadata=metadata)

            md_path = converter.output_dir / "article.md"
            content = md_path.read_text()
            assert "title: My Great Article\n" in content

    def test_date_from_html_meta(self):
        """Date should be extracted with full datetime from HTML meta tags."""
        with tempfile.TemporaryDirectory() as tmpdir:
            input_dir = os.path.join(tmpdir, "input")
            os.makedirs(input_dir)
            # Use article-like slug to avoid section redirect to _index.md
            filepath = os.path.join(input_dir, "breaking-news-story-b1234567.html")
            html = self._make_html(
                title="News Story",
                date_meta="2026-02-15T10:30:00Z",
                description="Breaking news",
            )
            with open(filepath, "w") as f:
                f.write(html)

            converter = HTMLToMarkdown(
                input_dir,
                base_url="https://news.example.com",
                domain="news.example.com",
            )
            converter.convert_file(Path(filepath))

            md_path = converter.output_dir / "breaking-news-story-b1234567.md"
            content = md_path.read_text()
            assert 'date: "2026-02-15T10:30:00+00:00"' in content
            assert "date_source: META_TAG\n" in content
