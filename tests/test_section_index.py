"""Tests for section index.md generation."""

import pytest
from scrapepy.convert.to_markdown import generate_section_index


class TestGenerateSectionIndex:
    def test_basic_index(self):
        articles = [
            {
                "title": "Great Match Report",
                "filename": "great-match-report.md",
                "date": "2026-02-25",
                "word_count": 1234,
                "description": "A report on the great match",
                "thumbnail": "thumb1.jpg",
            },
        ]
        result = generate_section_index(
            section_label="Sport",
            section_url="https://example.com/sport",
            domain="example.com",
            articles=articles,
            sub_sections=[],
        )
        assert result.startswith("---\n")
        assert "title: Sport" in result
        assert "page_type: section\n" in result
        assert "articles: 1" in result
        assert "# Sport" in result
        assert "[Great Match Report](great-match-report.md)" in result
        assert "![](images/thumb1.jpg)" in result
        assert "1,234 words" in result
        assert "A report on the great match" in result

    def test_with_sub_sections(self):
        result = generate_section_index(
            section_label="Sport",
            section_url="https://example.com/sport",
            domain="example.com",
            articles=[],
            sub_sections=[
                {"label": "Football", "dir_name": "football"},
                {"label": "Cricket", "dir_name": "cricket"},
            ],
        )
        assert "## Sub-sections" in result
        assert "[Football](football/_index.md)" in result
        assert "[Cricket](cricket/_index.md)" in result

    def test_no_articles_no_sub_sections(self):
        result = generate_section_index(
            section_label="Empty",
            section_url="https://example.com/empty",
            domain="example.com",
            articles=[],
            sub_sections=[],
        )
        assert "# Empty" in result
        assert "articles: 0" in result

    def test_article_without_thumbnail(self):
        articles = [
            {
                "title": "No Image Article",
                "filename": "no-image.md",
                "date": "2026-02-25",
                "word_count": 500,
                "description": "An article without an image",
                "thumbnail": None,
            },
        ]
        result = generate_section_index(
            section_label="News",
            section_url="https://example.com/news",
            domain="example.com",
            articles=articles,
            sub_sections=[],
        )
        assert "[No Image Article](no-image.md)" in result
        assert "![" not in result

    def test_thumbnail_with_images_prefix(self):
        """Thumbnails already prefixed with images/ should not be double-prefixed."""
        articles = [
            {
                "title": "Test",
                "filename": "test.md",
                "date": "2026-02-25",
                "word_count": 100,
                "description": None,
                "thumbnail": "images/already-prefixed.jpg",
            },
        ]
        result = generate_section_index(
            section_label="Test",
            section_url="https://example.com/test",
            domain="example.com",
            articles=articles,
            sub_sections=[],
        )
        assert "![](images/already-prefixed.jpg)" in result
        assert "images/images/" not in result

    def test_article_without_description(self):
        articles = [
            {
                "title": "Brief",
                "filename": "brief.md",
                "date": "2026-02-25",
                "word_count": 300,
                "description": None,
                "thumbnail": None,
            },
        ]
        result = generate_section_index(
            section_label="Test",
            section_url="https://example.com/test",
            domain="example.com",
            articles=articles,
            sub_sections=[],
        )
        assert "[Brief](brief.md)" in result
        assert "300 words" in result

    def test_frontmatter_url_quoted(self):
        """URLs with colons should be quoted in frontmatter."""
        result = generate_section_index(
            section_label="Test",
            section_url="https://example.com/test",
            domain="example.com",
            articles=[],
            sub_sections=[],
        )
        assert 'url: "https://example.com/test"' in result

    def test_multiple_articles_ordered(self):
        """Articles should appear in the order provided."""
        articles = [
            {
                "title": "First Article",
                "filename": "first.md",
                "date": "2026-02-25",
                "word_count": 500,
                "description": "First desc",
                "thumbnail": None,
            },
            {
                "title": "Second Article",
                "filename": "second.md",
                "date": "2026-02-24",
                "word_count": 700,
                "description": "Second desc",
                "thumbnail": None,
            },
        ]
        result = generate_section_index(
            section_label="News",
            section_url="https://example.com/news",
            domain="example.com",
            articles=articles,
            sub_sections=[],
        )
        assert "articles: 2" in result
        first_pos = result.index("[First Article]")
        second_pos = result.index("[Second Article]")
        assert first_pos < second_pos

    def test_sub_sections_before_articles(self):
        """Sub-sections heading should appear before Articles heading."""
        articles = [
            {
                "title": "An Article",
                "filename": "article.md",
                "date": "2026-02-25",
                "word_count": 200,
                "description": None,
                "thumbnail": None,
            },
        ]
        sub_sections = [
            {"label": "Sub One", "dir_name": "sub-one"},
        ]
        result = generate_section_index(
            section_label="Main",
            section_url="https://example.com/main",
            domain="example.com",
            articles=articles,
            sub_sections=sub_sections,
        )
        sub_pos = result.index("## Sub-sections")
        art_pos = result.index("## Articles")
        assert sub_pos < art_pos

    def test_article_date_and_word_count_on_same_line(self):
        """Date and word count should be on the same metadata line."""
        articles = [
            {
                "title": "Test",
                "filename": "test.md",
                "date": "2026-02-25",
                "word_count": 1000,
                "description": None,
                "thumbnail": None,
            },
        ]
        result = generate_section_index(
            section_label="Test",
            section_url="https://example.com/test",
            domain="example.com",
            articles=articles,
            sub_sections=[],
        )
        assert "25 Feb 2026 | 1,000 words" in result

    def test_article_without_date(self):
        """Articles without a date should only show word count."""
        articles = [
            {
                "title": "Undated",
                "filename": "undated.md",
                "date": None,
                "word_count": 400,
                "description": None,
                "thumbnail": None,
            },
        ]
        result = generate_section_index(
            section_label="Test",
            section_url="https://example.com/test",
            domain="example.com",
            articles=articles,
            sub_sections=[],
        )
        assert "400 words" in result
        assert "Published:" not in result

    def test_large_word_count_formatting(self):
        """Word counts should be comma-formatted."""
        articles = [
            {
                "title": "Long Read",
                "filename": "long.md",
                "date": "2026-01-01",
                "word_count": 12345,
                "description": None,
                "thumbnail": None,
            },
        ]
        result = generate_section_index(
            section_label="Test",
            section_url="https://example.com/test",
            domain="example.com",
            articles=articles,
            sub_sections=[],
        )
        assert "12,345 words" in result


class TestSectionIndexFrontmatter:
    """_index.md uses the same lowercase keys and values as other pages."""

    def _index(self):
        return generate_section_index(
            section_label="Sport",
            section_url="https://example.com/sport",
            domain="example.com",
            articles=[{"title": "A", "filename": "a.md"}],
            sub_sections=[],
            scraped_at="2026-01-01T00:00:00+00:00",
        )

    def test_keys_and_values_lowercase(self):
        result = self._index()
        fm = result.split("\n---\n", 1)[0].removeprefix("---\n")
        for line in fm.split("\n"):
            key = line.split(":", 1)[0]
            assert key == key.lower()
        assert "SECTION" not in result

    def test_scraped_at_and_hash(self):
        import hashlib
        result = self._index()
        assert 'scraped_at: "2026-01-01T00:00:00+00:00"\n' in result
        body = result.split("\n---\n", 1)[1][1:]
        assert f'content_hash: "sha256:{hashlib.sha256(body.encode()).hexdigest()}"\n' in result

    def test_body_starts_with_heading(self):
        assert self._index().split("\n---\n", 1)[1].startswith("\n# Sport\n")
