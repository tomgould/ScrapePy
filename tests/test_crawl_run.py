"""End-to-end crawl tests with the network replaced by in-memory pages.

These cover robots.txt enforcement in the crawl loop, request pacing,
run_summary.json, incremental change tracking and --no-index-pages.
"""

import asyncio
import json
import os

import pytest

from scrapepy.config import ScrapeConfig
from scrapepy.modes import crawl
from scrapepy.output.run_summary import EXIT_NOTHING_FETCHED, EXIT_OK, SUMMARY_FILENAME
from scrapepy.output.writer import create_output_dirs
from scrapepy.processing.robots import RobotsPolicy

BASE = "https://example.com"
WORDS = "Plenty of readable words appear here so the page passes the length check. " * 6


def page(title, links=(), body=WORDS):
    anchors = "".join(f'<a href="{href}">{href}</a> ' for href in links)
    return (
        f"<html><head><title>{title}</title></head><body>"
        f"<nav>{anchors}</nav><article><h1>{title}</h1><p>{body}</p></article>"
        f"</body></html>"
    )


class FakeSite:
    def __init__(self, pages, robots=None):
        self.pages = dict(pages)
        self.robots = robots
        self.requested: list[str] = []

    def fetch(self, url, config, headers=None):
        self.requested.append(url)
        path = url[len(BASE):] or "/"
        if path not in self.pages:
            return "", 404, {"headers": {}}
        return self.pages[path], 200, {"headers": {}}


@pytest.fixture
def site(monkeypatch):
    holder = {}

    def install(pages, robots=None):
        fake = FakeSite(pages, robots)
        holder["site"] = fake
        monkeypatch.setattr(crawl, "_fetch_with_http", fake.fetch)

        def load(config):
            return RobotsPolicy.from_text(
                fake.robots, user_agent=config.user_agent, base_url=config.base_url,
                enforced=not config.ignore_robots,
            ), fake.robots
        monkeypatch.setattr(crawl, "load_robots_policy", load)

        async def no_discovery(config, extra_sitemaps=None, robots=None, limiter=None):
            return {}, None, []
        monkeypatch.setattr(crawl, "run_discovery", no_discovery)

        async def no_assets(*args, **kwargs):
            return None
        monkeypatch.setattr(crawl, "discover_and_download_assets", no_assets)
        return fake

    return install


def run(tmp_path, **kw):
    kw.setdefault("wait", 0)
    config = ScrapeConfig(url=BASE + "/", output_root=str(tmp_path), **kw)
    create_output_dirs(config)
    return config, asyncio.run(crawl.run_crawl_mode(config))


SITE = {
    "/": page("Home", ["/about", "/private/secret", "/missing"]),
    "/about": page("About"),
    "/private/secret": page("Secret"),
}
ROBOTS = "User-agent: *\nDisallow: /private/\n"


class TestRobotsInCrawl:
    def test_disallowed_page_is_not_requested(self, site, tmp_path):
        fake = site(SITE, ROBOTS)
        _, summary = run(tmp_path)
        assert BASE + "/private/secret" not in fake.requested
        assert summary["blocked_by_robots"] == [BASE + "/private/secret"]
        assert summary["counts"]["blocked_by_robots"] == 1

    def test_ignore_robots_fetches_it(self, site, tmp_path):
        fake = site(SITE, ROBOTS)
        _, summary = run(tmp_path, ignore_robots=True)
        assert BASE + "/private/secret" in fake.requested
        assert summary["counts"]["blocked_by_robots"] == 0

    def test_missing_robots_allows_everything(self, site, tmp_path):
        fake = site(SITE, None)
        run(tmp_path)
        assert BASE + "/private/secret" in fake.requested

    def test_user_agent_group_applies(self, site, tmp_path):
        fake = site(SITE, "User-agent: PoliteBot\nDisallow: /about\n")
        run(tmp_path, user_agent="PoliteBot/1.0")
        assert BASE + "/about" not in fake.requested
        assert BASE + "/private/secret" in fake.requested

    def test_start_url_blocked_exits_non_zero(self, site, tmp_path):
        fake = site(SITE, "User-agent: *\nDisallow: /\n")
        _, summary = run(tmp_path)
        assert fake.requested == []
        assert summary["counts"]["fetched"] == 0
        assert summary["exit_code"] == EXIT_NOTHING_FETCHED


class TestPacing:
    def test_crawl_delay_sets_limiter_interval(self, site, tmp_path, monkeypatch):
        intervals = []

        class RecordingLimiter:
            def __init__(self, min_interval):
                intervals.append(min_interval)
                self.waits = 0

            async def wait(self, url):
                self.waits += 1

        monkeypatch.setattr(crawl, "HostRateLimiter", RecordingLimiter)
        site(SITE, "User-agent: *\nCrawl-delay: 7\n")
        run(tmp_path, wait=2)
        # Discovery limiter and page limiter both honour Crawl-delay
        assert intervals == [7.0, 7.0]

    def test_wait_time_is_floor_without_crawl_delay(self, site, tmp_path, monkeypatch):
        intervals = []

        class RecordingLimiter:
            def __init__(self, min_interval):
                intervals.append(min_interval)

            async def wait(self, url):
                pass

        monkeypatch.setattr(crawl, "HostRateLimiter", RecordingLimiter)
        site(SITE, None)
        run(tmp_path, wait=2)
        assert intervals == [1.0, 2.0]

    def test_every_page_fetch_waits_on_limiter(self, site, tmp_path, monkeypatch):
        waited: list[str] = []

        class RecordingLimiter:
            def __init__(self, min_interval):
                pass

            async def wait(self, url):
                waited.append(url)

        monkeypatch.setattr(crawl, "HostRateLimiter", RecordingLimiter)
        fake = site(SITE, ROBOTS)
        run(tmp_path)
        assert sorted(waited) == sorted(fake.requested)


class TestRunSummaryFile:
    def test_written_with_counts(self, site, tmp_path):
        site(SITE, ROBOTS)
        config, summary = run(tmp_path)
        path = os.path.join(config.output_dir, SUMMARY_FILENAME)
        with open(path, encoding="utf-8") as f:
            on_disk = json.load(f)
        assert on_disk == summary
        counts = summary["counts"]
        assert counts["fetched"] == 3
        assert counts["new"] == 2
        assert counts["failed"] == 1
        assert summary["failed"] == [{"url": BASE + "/missing", "reason": "HTTP 404"}]
        # The homepage is classified as a section, so it becomes _index.md
        assert sorted(summary["new_markdown"]) == [
            "MarkdownSites/example_com/_index.md",
            "MarkdownSites/example_com/about.md",
        ]
        assert summary["exit_code"] == EXIT_OK

    def test_second_run_reports_unchanged(self, site, tmp_path):
        site(SITE, ROBOTS)
        run(tmp_path)
        _, summary = run(tmp_path)
        counts = summary["counts"]
        assert counts["unchanged"] == 2
        assert counts["new"] == 0
        assert counts["changed"] == 0
        assert sorted(summary["unchanged_urls"]) == [BASE + "/", BASE + "/about"]
        assert summary["exit_code"] == EXIT_OK

    def test_changed_page_is_listed(self, site, tmp_path):
        fake = site(SITE, ROBOTS)
        run(tmp_path)
        fake.pages["/about"] = page("About", body=WORDS + " New paragraph added today.")
        _, summary = run(tmp_path)
        assert summary["changed_markdown"] == ["MarkdownSites/example_com/about.md"]
        assert summary["counts"]["unchanged"] == 1

    def test_no_incremental_marks_existing_as_changed(self, site, tmp_path):
        site(SITE, ROBOTS)
        run(tmp_path, incremental=False)
        _, summary = run(tmp_path, incremental=False)
        assert summary["counts"]["changed"] == 2
        assert summary["counts"]["new"] == 0

    def test_every_fetch_failed_exits_non_zero(self, site, tmp_path):
        site({}, None)
        _, summary = run(tmp_path)
        assert summary["counts"]["fetched"] == 1
        assert summary["counts"]["failed"] == 1
        assert summary["exit_code"] == EXIT_NOTHING_FETCHED

    def test_markdown_has_new_frontmatter(self, site, tmp_path):
        site(SITE, ROBOTS)
        config, _ = run(tmp_path)
        with open(os.path.join(config.markdown_dir, "about.md"), encoding="utf-8") as f:
            text = f.read()
        assert "\nscraped_at: " in text
        assert '\ncontent_hash: "sha256:' in text
        assert "null" not in text.split("\n---\n", 1)[0]


SECTION_SITE = {
    "/": page("Home", ["/news"], body="Short home."),
    "/news": page("News", ["/news/a-long-article-slug-about-things"], body="Short list."),
    "/news/a-long-article-slug-about-things": page("Article"),
}


class TestIndexPages:
    def test_index_page_generated_by_default(self, site, tmp_path):
        site(SECTION_SITE, None)
        config, _ = run(tmp_path, since="2000-01-01")
        assert os.path.exists(os.path.join(config.markdown_dir, "news", "_index.md"))
        with open(os.path.join(config.markdown_dir, "news", "_index.md"), encoding="utf-8") as f:
            text = f.read()
        assert "page_type: section\n" in text

    def test_no_index_pages_skips_listing(self, site, tmp_path):
        site(SECTION_SITE, None)
        config, summary = run(tmp_path, since="2000-01-01", index_pages=False)
        assert not os.path.exists(os.path.join(config.markdown_dir, "news", "_index.md"))
        # The article itself is still written
        assert any(p.endswith("a-long-article-slug-about-things.md") for p in summary["new_markdown"])


class TestPrefetchPacing:
    """Section batches prefetch articles; each prefetch must wait on the limiter."""

    ARTICLES = [f"/news/a-long-article-slug-number-{i}" for i in range(5)]

    def test_prefetched_articles_wait_on_limiter(self, site, tmp_path, monkeypatch):
        waited: list[str] = []

        class RecordingLimiter:
            def __init__(self, min_interval):
                pass

            async def wait(self, url):
                waited.append(url)

        monkeypatch.setattr(crawl, "HostRateLimiter", RecordingLimiter)
        pages = {
            "/": page("Home", ["/news"], body="Short home."),
            "/news": page("News", self.ARTICLES, body="Short list."),
        }
        pages.update({p: page(f"Article {i}") for i, p in enumerate(self.ARTICLES)})
        fake = site(pages, None)
        run(tmp_path, since="2000-01-01", concurrent=4)
        assert all(BASE + p in fake.requested for p in self.ARTICLES)
        assert sorted(waited) == sorted(fake.requested)

    def test_prefetch_skips_disallowed_articles(self, site, tmp_path):
        pages = {
            "/": page("Home", ["/news"], body="Short home."),
            "/news": page("News", self.ARTICLES, body="Short list."),
        }
        pages.update({p: page(f"Article {i}") for i, p in enumerate(self.ARTICLES)})
        fake = site(pages, "User-agent: *\nDisallow: /news/a-long-article-slug-number-2\n")
        _, summary = run(tmp_path, since="2000-01-01", concurrent=4)
        assert BASE + self.ARTICLES[2] not in fake.requested
        assert BASE + self.ARTICLES[2] in summary["blocked_by_robots"]
