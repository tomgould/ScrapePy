"""--markdown-only: Markdown and run state only, no HTML mirror, no asset requests."""

import asyncio
import json
import os

import pytest
import yaml

from scrapepy.cli import parse_args
from scrapepy.config import ScrapeConfig
from scrapepy.modes import crawl
from scrapepy.output.run_summary import SUMMARY_FILENAME
from scrapepy.output.writer import create_output_dirs
from tests.test_crawl_run import (  # noqa: F401  (pytest fixtures)
    BASE, WORDS, RecordingLimiter, page, recorder, site,
)


def rich_page(title, links=(), extra=""):
    anchors = "".join(f'<a href="{href}">{href}</a> ' for href in links)
    return (
        f"<html><head><title>{title}</title></head><body><nav>{anchors}</nav>"
        f"<article><h1>{title}</h1><p>{WORDS}</p>"
        f'<p>See <a href="/about">about us</a> and <a href="page2">the next page</a>.</p>'
        f'<img src="/img/photo.png" alt="A photo">{extra}</article></body></html>'
    )


SITE = {
    "/": rich_page("Home", ["/about", "/private/x"]),
    "/about": rich_page("About"),
    "/private/x": rich_page("Private"),
}


@pytest.fixture
def no_assets(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("markdown-only must not touch assets")
    monkeypatch.setattr(crawl, "create_asset_client", forbidden)
    monkeypatch.setattr(crawl, "discover_and_download_assets", forbidden)


def run_md_only(tmp_path, **kw):
    kw.setdefault("wait", 0)
    config = ScrapeConfig(url=BASE + "/", output_root=str(tmp_path), markdown_only=True, **kw)
    create_output_dirs(config)
    return config, asyncio.run(crawl.run_crawl_mode(config))


def frontmatter(path):
    text = open(path, encoding="utf-8").read()
    return yaml.safe_load(text.split("\n---\n", 1)[0].removeprefix("---\n")), text


def all_frontmatter(config):
    """Frontmatter of every Markdown file the run wrote."""
    out = []
    for d, _, files in os.walk(config.markdown_dir):
        for f in files:
            out.append(frontmatter(os.path.join(d, f))[0])
    return out


class TestOutputLayout:
    def test_only_markdown_and_state_files(self, site, tmp_path, no_assets):
        site(SITE, "User-agent: *\nDisallow: /private/\n")
        config, summary = run_md_only(tmp_path)
        state_files = set(os.listdir(config.output_dir))
        # page2 is a dead link, so broken_links.txt is written too
        assert state_files == {".scrapepy_cache.json", SUMMARY_FILENAME, "broken_links.txt"}
        assert state_files <= {".scrapepy_cache.json", SUMMARY_FILENAME, "broken_links.txt",
                               "site_outline.json"}
        md_files = sorted(
            os.path.relpath(os.path.join(d, f), config.markdown_dir)
            for d, _, files in os.walk(config.markdown_dir) for f in files
        )
        assert md_files == ["_index.md", "about.md"]
        assert summary["options"]["markdown_only"] is True
        assert summary["counts"]["new"] == 2

    def test_links_and_images_are_absolute(self, site, tmp_path, no_assets):
        site(SITE, None)
        config, _ = run_md_only(tmp_path)
        _, text = frontmatter(os.path.join(config.markdown_dir, "about.md"))
        assert "[about us](https://example.com/about)" in text
        assert "[the next page](https://example.com/page2)" in text
        assert "![A photo](https://example.com/img/photo.png)" in text
        assert "](/" not in text and ".md)" not in text

    def test_frontmatter_is_valid_yaml(self, site, tmp_path, no_assets):
        site(SITE, None)
        config, _ = run_md_only(tmp_path)
        data, _ = frontmatter(os.path.join(config.markdown_dir, "about.md"))
        assert data["url"] == BASE + "/about"
        assert data["title"] == "About"


class TestIncremental:
    def test_second_run_reports_unchanged(self, site, tmp_path, no_assets):
        site(SITE, None)
        run_md_only(tmp_path)
        _, summary = run_md_only(tmp_path)
        assert summary["counts"]["unchanged"] == 3
        assert summary["counts"]["new"] == 0
        assert summary["counts"]["changed"] == 0

    def test_changed_page_detected(self, site, tmp_path, no_assets):
        fake = site(SITE, None)
        run_md_only(tmp_path)
        fake.pages["/about"] = rich_page("About", extra="<p>Brand new paragraph today.</p>")
        _, summary = run_md_only(tmp_path)
        assert summary["changed_markdown"] == ["MarkdownSites/example_com/about.md"]

    def test_without_cache_existing_markdown_counts_as_changed(self, site, tmp_path, no_assets):
        site(SITE, None)
        run_md_only(tmp_path, incremental=False)
        _, summary = run_md_only(tmp_path, incremental=False)
        assert summary["counts"]["new"] == 0
        assert summary["counts"]["changed"] == 3


class TestEveryRequestIsCheckedAndPaced:
    def test_all_requests_wait_on_the_shared_limiter(self, site, tmp_path, recorder, no_assets):
        fake = site(SITE, "User-agent: *\nDisallow: /private/\n")
        run_md_only(tmp_path)
        assert len(recorder) == 1
        assert sorted(recorder[0].waited) == sorted(fake.requested)
        assert BASE + "/private/x" not in fake.requested


class TestDuplicatesAndCanonical:
    def test_identical_pages_become_one_file(self, site, tmp_path, no_assets):
        body = rich_page("Same page")
        site({"/": page("Home", ["/a", "/a-copy"]), "/a": body, "/a-copy": body}, None)
        config, summary = run_md_only(tmp_path)
        assert os.path.exists(os.path.join(config.markdown_dir, "a.md"))
        assert not os.path.exists(os.path.join(config.markdown_dir, "a-copy.md"))
        assert summary["counts"]["duplicates"] == 1
        assert summary["duplicates"] == [
            {"url": BASE + "/a-copy", "same_as": "MarkdownSites/example_com/a.md"}
        ]
        assert BASE + "/a-copy" not in summary["new_markdown"]

    def test_canonical_is_recorded_but_url_stays_fetched(self, site, tmp_path, no_assets):
        canon = '<link rel="canonical" href="/about-us">'
        site({"/": page("Home", ["/about"]),
              "/about": rich_page("About").replace("<head>", "<head>" + canon)}, None)
        config, _ = run_md_only(tmp_path)
        data, _ = frontmatter(os.path.join(config.markdown_dir, "about.md"))
        assert data["url"] == BASE + "/about"
        assert data["canonical_url"] == BASE + "/about-us"
        assert "fetched_url" not in data

    def test_canonical_pointing_at_another_page_keeps_own_url(self, site, tmp_path, no_assets):
        canon = '<link rel="canonical" href="/">'
        site({"/": rich_page("Home", ["/about"]),
              "/about": rich_page("About").replace("<head>", "<head>" + canon)}, None)
        config, _ = run_md_only(tmp_path)
        home, _ = frontmatter(os.path.join(config.markdown_dir, "_index.md"))
        about, _ = frontmatter(os.path.join(config.markdown_dir, "about.md"))
        assert home["url"] == BASE + "/"
        assert about["url"] == BASE + "/about"
        assert about["canonical_url"] == BASE + "/"

    def test_pages_sharing_a_canonical_keep_distinct_urls(self, site, tmp_path, no_assets):
        canon = '<link rel="canonical" href="https://example.com/guide">'
        site({"/": page("Home", ["/guide-a", "/guide-b"]),
              "/guide-a": rich_page("Guide A").replace("<head>", "<head>" + canon),
              "/guide-b": rich_page("Guide B").replace("<head>", "<head>" + canon)}, None)
        config, _ = run_md_only(tmp_path)
        pages = {d["title"]: d for d in all_frontmatter(config)}
        assert pages["Guide A"]["url"] == BASE + "/guide-a"
        assert pages["Guide B"]["url"] == BASE + "/guide-b"
        assert pages["Guide A"]["canonical_url"] == BASE + "/guide"
        assert pages["Guide B"]["canonical_url"] == BASE + "/guide"

    def test_pagination_pages_keep_their_own_urls(self, site, tmp_path, no_assets):
        canon = '<link rel="canonical" href="/news/page/1">'
        site({"/": page("Home", ["/news/page/1", "/news/page/2", "/news/page/3"]),
              "/news/page/1": rich_page("News 1").replace("<head>", "<head>" + canon),
              "/news/page/2": rich_page("News 2").replace("<head>", "<head>" + canon),
              "/news/page/3": rich_page("News 3").replace("<head>", "<head>" + canon)}, None)
        config, _ = run_md_only(tmp_path)
        urls = [d["url"] for d in all_frontmatter(config)]
        assert len(urls) == len(set(urls))
        assert {BASE + "/news/page/1", BASE + "/news/page/2", BASE + "/news/page/3"} <= set(urls)

    def test_canonical_on_another_scheme_is_ignored(self, site, tmp_path, no_assets):
        # The crawl is over https; an http canonical is a different site
        canon = '<link rel="canonical" href="http://example.com/about-us">'
        site({"/": page("Home", ["/about"]),
              "/about": rich_page("About").replace("<head>", "<head>" + canon)}, None)
        config, _ = run_md_only(tmp_path)
        data, _ = frontmatter(os.path.join(config.markdown_dir, "about.md"))
        assert BASE.startswith("https://")
        assert data["url"] == BASE + "/about"
        assert "canonical_url" not in data

    def test_cross_site_canonical_is_ignored(self, site, tmp_path, no_assets):
        canon = '<link rel="canonical" href="https://other.example.org/x">'
        site({"/": page("Home", ["/about"]),
              "/about": rich_page("About").replace("<head>", "<head>" + canon)}, None)
        config, _ = run_md_only(tmp_path)
        data, _ = frontmatter(os.path.join(config.markdown_dir, "about.md"))
        assert data["url"] == BASE + "/about"
        assert "canonical_url" not in data


class TestNoAssetDownloads:
    def test_markdown_only_never_downloads_assets(self, site, tmp_path, monkeypatch):
        site(SITE, None)
        calls = []

        async def recording(*args, **kwargs):
            calls.append("download")

        def recording_client(*args, **kwargs):
            calls.append("client")
            raise AssertionError("markdown-only must not open an asset client")

        # Patched after site(), which installs its own no-op downloader
        monkeypatch.setattr(crawl, "discover_and_download_assets", recording)
        monkeypatch.setattr(crawl, "create_asset_client", recording_client)
        config, summary = run_md_only(tmp_path)
        assert summary["counts"]["new"] >= 1
        assert calls == []


class TestCli:
    def test_flag(self):
        config = parse_args(["https://example.com", "--markdown-only"])
        assert config.markdown_only and config.markdown and not config.include_assets

    @pytest.mark.parametrize("extra", [["--no-markdown"], ["-1"], ["--swagger"], ["-d"], ["--audit"]])
    def test_incompatible_flags(self, extra):
        with pytest.raises(SystemExit):
            parse_args(["https://example.com", "--markdown-only", *extra])

    def test_js_render_allowed(self):
        assert parse_args(["https://example.com", "--markdown-only", "-j"]).js_render
