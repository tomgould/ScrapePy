"""End-to-end crawl tests with the network replaced by in-memory pages.

These cover robots.txt in the crawl loop (applied only with obey_robots, the
--obey-robots flag), request pacing, run_summary.json, incremental change
tracking and --no-index-pages.
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
                enforced=config.obey_robots,
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


def run(tmp_path, relative_to=None, **kw):
    """Run a crawl into tmp_path. With relative_to (a folder that is the
    current directory), -o is given as a path relative to it, as a user
    typing "-o data" would."""
    kw.setdefault("wait", 0)
    root = os.path.relpath(tmp_path, relative_to) if relative_to else str(tmp_path)
    config = ScrapeConfig(url=BASE + "/", output_root=root, **kw)
    create_output_dirs(config)
    return config, asyncio.run(crawl.run_crawl_mode(config))


SITE = {
    "/": page("Home", ["/about", "/private/secret", "/missing"]),
    "/about": page("About"),
    "/private/secret": page("Secret"),
}
ROBOTS = "User-agent: *\nDisallow: /private/\n"


class TestRobotsInCrawl:
    def test_obey_robots_does_not_request_a_disallowed_page(self, site, tmp_path):
        fake = site(SITE, ROBOTS)
        _, summary = run(tmp_path, obey_robots=True)
        assert BASE + "/private/secret" not in fake.requested
        assert summary["blocked_by_robots"] == [BASE + "/private/secret"]
        assert summary["counts"]["blocked_by_robots"] == 1

    def test_default_fetches_a_disallowed_page(self, site, tmp_path):
        fake = site(SITE, ROBOTS)
        _, summary = run(tmp_path)
        assert BASE + "/private/secret" in fake.requested
        assert summary["blocked_by_robots"] == []
        assert summary["counts"]["blocked_by_robots"] == 0

    def test_default_fetches_a_page_when_robots_disallows_everything(self, site, tmp_path):
        fake = site(SITE, "User-agent: *\nDisallow: /\n")
        _, summary = run(tmp_path)
        assert BASE + "/" in fake.requested
        assert BASE + "/private/secret" in fake.requested
        assert summary["exit_code"] == EXIT_OK

    def test_missing_robots_allows_everything(self, site, tmp_path):
        fake = site(SITE, None)
        run(tmp_path, obey_robots=True)
        assert BASE + "/private/secret" in fake.requested

    def test_user_agent_group_applies(self, site, tmp_path):
        fake = site(SITE, "User-agent: PoliteBot\nDisallow: /about\n")
        run(tmp_path, user_agent="PoliteBot/1.0", obey_robots=True)
        assert BASE + "/about" not in fake.requested
        assert BASE + "/private/secret" in fake.requested

    def test_start_url_blocked_exits_non_zero(self, site, tmp_path):
        fake = site(SITE, "User-agent: *\nDisallow: /\n")
        _, summary = run(tmp_path, obey_robots=True)
        assert fake.requested == []
        assert summary["counts"]["fetched"] == 0
        assert summary["exit_code"] == EXIT_NOTHING_FETCHED


class RecordingLimiter:
    """Stands in for HostRateLimiter and records how it is used."""

    created: list["RecordingLimiter"] = []

    def __init__(self, min_interval):
        self.min_interval = min_interval
        self.view_intervals: list[float] = []
        self.waited: list[str] = []
        RecordingLimiter.created.append(self)

    async def wait(self, url, interval=None):
        self.waited.append(url)

    def reserve(self, host, interval=None):
        self.reserved = getattr(self, "reserved", []) + [host]
        return 0

    def with_interval(self, interval):
        self.view_intervals.append(interval)
        parent = self

        class View:
            min_interval = interval

            async def wait(self, url):
                parent.waited.append(url)
        return View()


@pytest.fixture
def recorder(monkeypatch):
    RecordingLimiter.created = []
    monkeypatch.setattr(crawl, "HostRateLimiter", RecordingLimiter)
    return RecordingLimiter.created


class TestPacing:
    def test_one_limiter_shared_by_discovery_and_pages(self, site, tmp_path, recorder):
        site(SITE, None)
        run(tmp_path, wait=2)
        assert len(recorder) == 1

    def test_robots_request_counts_towards_the_gap(self, site, tmp_path, recorder):
        site(SITE, None)
        run(tmp_path, wait=2)
        assert recorder[0].reserved == ["example.com"]

    def test_crawl_delay_sets_both_intervals_with_obey_robots(self, site, tmp_path, recorder):
        site(SITE, "User-agent: *\nCrawl-delay: 7\n")
        run(tmp_path, wait=2, obey_robots=True)
        assert recorder[0].min_interval == 7.0
        assert recorder[0].view_intervals == [7.0]

    def test_default_ignores_crawl_delay(self, site, tmp_path, recorder):
        site(SITE, "User-agent: *\nCrawl-delay: 7\n")
        run(tmp_path, wait=2)
        assert recorder[0].min_interval == 2.0
        assert recorder[0].view_intervals == [1.0]

    def test_default_keeps_the_wait_floor_with_a_shorter_crawl_delay(self, site, tmp_path, recorder):
        site(SITE, "User-agent: *\nCrawl-delay: 1\n")
        run(tmp_path, wait=2)
        assert recorder[0].min_interval == 2.0

    def test_wait_time_is_floor_without_crawl_delay(self, site, tmp_path, recorder):
        site(SITE, None)
        run(tmp_path, wait=2)
        assert recorder[0].min_interval == 2.0
        assert recorder[0].view_intervals == [1.0]

    @pytest.mark.parametrize("obey", [False, True])
    def test_every_page_fetch_waits_on_limiter(self, site, tmp_path, recorder, obey):
        fake = site(SITE, ROBOTS)
        run(tmp_path, obey_robots=obey)
        assert sorted(recorder[0].waited) == sorted(fake.requested)


class TestRunSummaryFile:
    def test_written_with_counts(self, site, tmp_path):
        site(SITE, ROBOTS)
        config, summary = run(tmp_path, obey_robots=True)
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
        run(tmp_path, obey_robots=True)
        _, summary = run(tmp_path, obey_robots=True)
        counts = summary["counts"]
        assert counts["unchanged"] == 2
        assert counts["new"] == 0
        assert counts["changed"] == 0
        assert sorted(summary["unchanged_urls"]) == [BASE + "/", BASE + "/about"]
        assert summary["exit_code"] == EXIT_OK

    def test_changed_page_is_listed(self, site, tmp_path):
        fake = site(SITE, ROBOTS)
        run(tmp_path, obey_robots=True)
        fake.pages["/about"] = page("About", body=WORDS + " New paragraph added today.")
        _, summary = run(tmp_path, obey_robots=True)
        assert summary["changed_markdown"] == ["MarkdownSites/example_com/about.md"]
        assert summary["counts"]["unchanged"] == 1

    def test_no_incremental_marks_existing_as_changed(self, site, tmp_path):
        site(SITE, ROBOTS)
        run(tmp_path, incremental=False, obey_robots=True)
        _, summary = run(tmp_path, incremental=False, obey_robots=True)
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
        assert "\ncontent_hash: sha256:" in text
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

    def test_prefetched_articles_wait_on_limiter(self, site, tmp_path, recorder):
        pages = {
            "/": page("Home", ["/news"], body="Short home."),
            "/news": page("News", self.ARTICLES, body="Short list."),
        }
        pages.update({p: page(f"Article {i}") for i, p in enumerate(self.ARTICLES)})
        fake = site(pages, None)
        run(tmp_path, since="2000-01-01", concurrent=4)
        assert all(BASE + p in fake.requested for p in self.ARTICLES)
        assert sorted(recorder[0].waited) == sorted(fake.requested)

    def test_prefetch_skips_disallowed_articles(self, site, tmp_path):
        pages = {
            "/": page("Home", ["/news"], body="Short home."),
            "/news": page("News", self.ARTICLES, body="Short list."),
        }
        pages.update({p: page(f"Article {i}") for i, p in enumerate(self.ARTICLES)})
        fake = site(pages, "User-agent: *\nDisallow: /news/a-long-article-slug-number-2\n")
        _, summary = run(tmp_path, since="2000-01-01", concurrent=4, obey_robots=True)
        assert BASE + self.ARTICLES[2] not in fake.requested
        assert BASE + self.ARTICLES[2] in summary["blocked_by_robots"]

    def test_prefetch_default_fetches_articles_robots_disallows(self, site, tmp_path):
        pages = {
            "/": page("Home", ["/news"], body="Short home."),
            "/news": page("News", self.ARTICLES, body="Short list."),
        }
        pages.update({p: page(f"Article {i}") for i, p in enumerate(self.ARTICLES)})
        fake = site(pages, "User-agent: *\nDisallow: /news/a-long-article-slug-number-2\n")
        _, summary = run(tmp_path, since="2000-01-01", concurrent=4)
        assert BASE + self.ARTICLES[2] in fake.requested
        assert summary["blocked_by_robots"] == []


class TestSameRunDuplicates:
    def test_root_and_index_html_count_once(self, site, tmp_path):
        site({
            "/": page("Home", ["/index.html", "/about"]),
            "/index.html": page("Home"),
            "/about": page("About"),
        }, None)
        _, summary = run(tmp_path)
        counts = summary["counts"]
        assert counts["fetched"] == 3
        assert counts["new"] == 2
        assert counts["changed"] == 0
        assert len(summary["new_markdown"]) == len(set(summary["new_markdown"]))


class TestRobotsUnavailable:
    def _install_status(self, site, monkeypatch, status):
        fake = site(SITE, None)
        from scrapepy.processing.robots import policy_from_response

        def load(config):
            return policy_from_response(status, None, config.user_agent, config.base_url,
                                        enforced=config.obey_robots), None
        monkeypatch.setattr(crawl, "load_robots_policy", load)
        return fake

    def test_5xx_fetches_nothing_and_says_why_with_obey_robots(self, site, tmp_path, monkeypatch,
                                                               capsys):
        fake = self._install_status(site, monkeypatch, 503)
        _, summary = run(tmp_path, obey_robots=True)
        assert fake.requested == []
        assert summary["exit_code"] == EXIT_NOTHING_FETCHED
        assert summary["blocked_by_robots"] == [BASE + "/"]
        out = capsys.readouterr().out
        assert "robots.txt could not be read (HTTP 503)" in out
        assert "Nothing will be crawled" in out
        assert "run without --obey-robots" in out

    def test_default_crawls_through_a_5xx_robots_txt(self, site, tmp_path, monkeypatch, capsys):
        fake = self._install_status(site, monkeypatch, 503)
        _, summary = run(tmp_path)
        assert BASE + "/about" in fake.requested
        assert summary["exit_code"] == EXIT_OK
        assert "robots" not in capsys.readouterr().out.lower()

    def test_404_allows_everything(self, site, tmp_path, monkeypatch):
        fake = self._install_status(site, monkeypatch, 404)
        run(tmp_path, obey_robots=True)
        assert BASE + "/private/secret" in fake.requested


class TestStaleSummary:
    def test_old_summary_replaced_before_crawling(self, site, tmp_path, monkeypatch):
        site(SITE, None)
        config = ScrapeConfig(url=BASE + "/", output_root=str(tmp_path), wait=0)
        os.makedirs(config.output_dir, exist_ok=True)
        path = os.path.join(config.output_dir, SUMMARY_FILENAME)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"status": "complete", "exit_code": 0, "stale": True}, f)
        seen = {}

        async def peek(config, extra_sitemaps=None, robots=None, limiter=None):
            with open(path, encoding="utf-8") as f:
                seen.update(json.load(f))
            return {}, None, []
        monkeypatch.setattr(crawl, "run_discovery", peek)
        create_output_dirs(config)
        asyncio.run(crawl.run_crawl_mode(config))
        assert seen["status"] == "running"
        assert "stale" not in seen
        with open(path, encoding="utf-8") as f:
            assert json.load(f)["status"] == "complete"


class TestCancellation:
    """Ctrl+C under asyncio.run() cancels the task; the crawl must still report."""

    def test_cancel_mid_crawl_writes_interrupted_summary(self, site, tmp_path, monkeypatch):
        import threading
        import time as _time

        links = [f"/p{i}" for i in range(20)]
        fake = site({"/": page("Home", links), **{p: page(p) for p in links}}, None)
        started = threading.Event()
        original = fake.fetch

        def slow_fetch(url, config, headers=None):
            if url != BASE + "/":
                started.set()
                _time.sleep(0.2)
            return original(url, config, headers)
        monkeypatch.setattr(crawl, "_fetch_with_http", slow_fetch)

        config = ScrapeConfig(url=BASE + "/", output_root=str(tmp_path), wait=0)
        create_output_dirs(config)

        async def main():
            task = asyncio.create_task(crawl.run_crawl_mode(config))
            while not started.is_set():
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            task.cancel()
            return await task

        summary = asyncio.run(main())
        assert summary["interrupted"] is True
        assert summary["status"] == "interrupted"
        assert summary["exit_code"] == 130
        assert summary["counts"]["fetched"] < 21
        with open(os.path.join(config.output_dir, SUMMARY_FILENAME), encoding="utf-8") as f:
            assert json.load(f)["status"] == "interrupted"
        with open(os.path.join(config.output_dir, ".scrapepy_cache.json"), encoding="utf-8") as f:
            assert BASE[len("https://example.com"):] + "/" in json.load(f)["pages"]


class TestSlowRobotsDoesNotBlockInterrupt:
    def test_cancel_while_robots_txt_is_slow(self, site, tmp_path, monkeypatch, capsys):
        import time as _time
        fake = site(SITE, None)

        def slow_load(config):
            _time.sleep(1.0)
            return RobotsPolicy.from_text(None, base_url=config.base_url), None
        monkeypatch.setattr(crawl, "load_robots_policy", slow_load)
        config = ScrapeConfig(url=BASE + "/", output_root=str(tmp_path), wait=0)
        create_output_dirs(config)

        async def main():
            task = asyncio.create_task(crawl.run_crawl_mode(config))
            await asyncio.sleep(0.1)
            start = _time.perf_counter()
            task.cancel()
            summary = await task
            return summary, _time.perf_counter() - start

        summary, elapsed = asyncio.run(main())
        assert elapsed < 0.5
        assert summary["status"] == "interrupted"
        assert fake.requested == []
        # The default does not apply robots.txt, so an interrupt says nothing about it
        out = capsys.readouterr().out
        assert "Crawl interrupted" in out
        assert "Nothing will be crawled" not in out
        assert "--obey-robots" not in out


class TestRelativeOutputDir:
    """The other tests pass an absolute tmp_path as -o, which hid a failure
    of every page when -o was relative."""

    def test_relative_output_dir_writes_markdown(self, site, tmp_path, monkeypatch):
        site({"/": page("Home", ["/about"]), "/about": page("About")})
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        config, summary = run(tmp_path / "out", relative_to=cwd, markdown_only=True)
        assert summary["counts"]["failed"] == 0
        assert summary["exit_code"] == EXIT_OK
        assert sorted(summary["new_markdown"]) == [
            "MarkdownSites/example_com/_index.md", "MarkdownSites/example_com/about.md"]
        assert summary["output_dir"] == str(tmp_path / "out")
