"""Tests for --output-dir and the derived HTML and Markdown folders."""

import os
from pathlib import Path

from scrapepy.cli import parse_args
from scrapepy.config import ScrapeConfig, derive_output_dirs
from scrapepy.convert.to_markdown import HTMLToMarkdown
from scrapepy.output.writer import (
    article_filepath_in_section,
    section_index_filepath,
    url_to_filepath,
)


class TestDeriveOutputDirs:
    def test_default_root_is_current_directory(self):
        html, md = derive_output_dirs(".", "example_com")
        assert html == os.path.join(".", "ScrapedSites", "example_com")
        assert md == os.path.join(".", "MarkdownSites", "example_com")

    def test_custom_root(self):
        html, md = derive_output_dirs("/data/out", "example_com")
        assert html == os.path.join("/data/out", "ScrapedSites", "example_com")
        assert md == os.path.join("/data/out", "MarkdownSites", "example_com")

    def test_empty_root_falls_back_to_current_directory(self):
        assert derive_output_dirs("", "x") == derive_output_dirs(".", "x")

    def test_root_containing_scrapedsites_is_not_rewritten(self):
        # The old code replaced "ScrapedSites" anywhere in the path.
        html, md = derive_output_dirs("/srv/ScrapedSites-archive", "site")
        assert md == os.path.join("/srv/ScrapedSites-archive", "MarkdownSites", "site")
        assert html == os.path.join("/srv/ScrapedSites-archive", "ScrapedSites", "site")


class TestConfigOutputDirs:
    def test_default_config_paths_unchanged(self):
        # The default -o is the current directory, resolved to an absolute path
        config = ScrapeConfig(url="https://www.example.com")
        assert config.output_dir == os.path.join(os.getcwd(), "ScrapedSites", "example_com")
        assert config.markdown_dir == os.path.join(os.getcwd(), "MarkdownSites", "example_com")

    def test_output_root(self, tmp_path):
        config = ScrapeConfig(url="https://example.com/docs", stay_in_path=True,
                              output_root=str(tmp_path))
        assert config.output_dir == str(tmp_path / "ScrapedSites" / "example_com_docs")
        assert config.markdown_dir == str(tmp_path / "MarkdownSites" / "example_com_docs")

    def test_instagram_uses_root(self, tmp_path):
        config = ScrapeConfig(url="", instagram=True, output_root=str(tmp_path))
        assert config.output_dir == str(tmp_path / "ScrapedSites" / "instagram")

    def test_cli_flag(self, tmp_path):
        config = parse_args(["https://example.com", "--output-dir", str(tmp_path)])
        assert config.output_root == str(tmp_path)
        assert config.output_dir.startswith(str(tmp_path))

    def test_cli_short_flag(self, tmp_path):
        config = parse_args(["https://example.com", "-o", str(tmp_path)])
        assert config.output_root == str(tmp_path)

    def test_cli_default(self):
        assert parse_args(["https://example.com"]).output_root == os.getcwd()


class TestWriterUsesMarkdownDir:
    def _config(self, tmp_path):
        return ScrapeConfig(url="https://example.com", output_root=str(tmp_path))

    def test_section_index_under_markdown_dir(self, tmp_path):
        config = self._config(tmp_path)
        path = section_index_filepath("/news", config)
        assert path == os.path.join(config.markdown_dir, "news", "_index.md")

    def test_article_under_markdown_dir(self, tmp_path):
        config = self._config(tmp_path)
        path = article_filepath_in_section("/news/my-long-article-slug", "/news", config)
        assert path == os.path.join(config.markdown_dir, "news", "my-long-article-slug.md")

    def test_html_under_output_dir(self, tmp_path):
        config = self._config(tmp_path)
        assert url_to_filepath("/about", config) == os.path.join(config.output_dir, "about.html")


class TestConverterOutputDir:
    def test_explicit_output_dir(self, tmp_path):
        conv = HTMLToMarkdown(tmp_path / "ScrapedSites" / "site", output_dir=tmp_path / "md")
        assert conv.output_dir == tmp_path / "md"

    def test_standalone_default_mirrors_layout(self, tmp_path):
        conv = HTMLToMarkdown(tmp_path / "ScrapedSites" / "site")
        assert conv.output_dir == Path(tmp_path / "MarkdownSites" / "site")


class TestSectionIndexNames:
    def test_index_html_section_is_its_folder(self, tmp_path):
        config = ScrapeConfig(url="https://example.com", output_root=str(tmp_path))
        assert section_index_filepath("/news/index.html", config) == \
            os.path.join(config.markdown_dir, "news", "_index.md")
        assert section_index_filepath("/index.html", config) == \
            os.path.join(config.markdown_dir, "_index.md")
        assert section_index_filepath("/news/indexes", config) == \
            os.path.join(config.markdown_dir, "news", "indexes", "_index.md")
