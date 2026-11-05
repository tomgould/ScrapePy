"""robots.txt checks in the browser modes (-1, --swagger, -d), with no browser or network."""

import asyncio

import pytest

from scrapepy import scraper
from scrapepy.config import ScrapeConfig
from scrapepy.modes import docs
from scrapepy.output.run_summary import EXIT_NOTHING_FETCHED, EXIT_OK
from scrapepy.processing import discovery
from scrapepy.processing.robots import RobotsPolicy, policy_from_response

BASE = "https://example.com"


class FakeResponse:
    def __init__(self, html):
        self.body = html.encode()
        self.encoding = "utf-8"
        self.status = 200


def _use_robots(monkeypatch, text, status=200):
    def load(config):
        return policy_from_response(status, text, config.user_agent, config.base_url,
                                    enforced=not config.ignore_robots), text
    monkeypatch.setattr(discovery, "load_robots_policy", load)


@pytest.fixture
def mode_calls(monkeypatch):
    """Replace the three browser modes with recorders that report one saved page."""
    calls: list[str] = []
    from scrapepy.modes import single_page, swagger

    def recorder(name):
        async def fake(config, **kwargs):
            calls.append(name)
            return 1
        return fake

    monkeypatch.setattr(single_page, "run_single_page_mode", recorder("single_page"))
    monkeypatch.setattr(swagger, "run_swagger_mode", recorder("swagger"))
    monkeypatch.setattr(docs, "run_docs_mode", recorder("docs"))
    return calls


MODES = [
    ("single_page", {"single_page": True}),
    ("swagger", {"swagger": True}),
    ("docs", {"docs": True}),
]


class TestStartUrlCheck:
    @pytest.mark.parametrize("name,flags", MODES)
    def test_disallowed_start_url_is_not_fetched(self, name, flags, monkeypatch, mode_calls, tmp_path):
        _use_robots(monkeypatch, "User-agent: *\nDisallow: /app\n")
        config = ScrapeConfig(url=BASE + "/app", output_root=str(tmp_path), markdown=False, **flags)
        assert asyncio.run(scraper.run_scraper(config)) == EXIT_NOTHING_FETCHED
        assert mode_calls == []

    @pytest.mark.parametrize("name,flags", MODES)
    def test_allowed_start_url_runs_mode(self, name, flags, monkeypatch, mode_calls, tmp_path):
        _use_robots(monkeypatch, "User-agent: *\nDisallow: /private\n")
        config = ScrapeConfig(url=BASE + "/app", output_root=str(tmp_path), markdown=False, **flags)
        assert asyncio.run(scraper.run_scraper(config)) == EXIT_OK
        assert mode_calls == [name]

    def test_unreadable_robots_blocks_with_reason(self, monkeypatch, mode_calls, tmp_path, capsys):
        _use_robots(monkeypatch, None, status=503)
        config = ScrapeConfig(url=BASE + "/app", output_root=str(tmp_path), markdown=False,
                              single_page=True)
        assert asyncio.run(scraper.run_scraper(config)) == EXIT_NOTHING_FETCHED
        assert "robots.txt could not be read (HTTP 503)" in capsys.readouterr().out
        assert mode_calls == []

    def test_ignore_robots_runs_mode(self, monkeypatch, mode_calls, tmp_path):
        _use_robots(monkeypatch, "User-agent: *\nDisallow: /\n")
        config = ScrapeConfig(url=BASE + "/app", output_root=str(tmp_path), markdown=False,
                              single_page=True, ignore_robots=True)
        assert asyncio.run(scraper.run_scraper(config)) == EXIT_OK
        assert mode_calls == ["single_page"]


HOME = f"""<html><head><title>Docs</title></head><body><nav>
<a href="/guide/start">Getting started</a>
<a href="/private/internal">Internal notes</a>
<a href="{BASE}/guide/api">API</a>
</nav></body></html>"""


class TestDocsModeLinks:
    def test_disallowed_sidebar_links_are_skipped(self, monkeypatch, tmp_path):
        fetched: list[str] = []

        async def fake_fetch(url, config, page_action=None, wait_time=None, user_data_dir=None):
            fetched.append(url)
            return FakeResponse(HOME if len(fetched) == 1 else f"<html><title>{url}</title></html>")

        monkeypatch.setattr(docs, "fetch_page_browser", fake_fetch)
        config = ScrapeConfig(url=BASE + "/", output_root=str(tmp_path), docs=True, wait=0)
        robots = RobotsPolicy.from_text("User-agent: *\nDisallow: /private/\n", base_url=BASE)
        saved = asyncio.run(docs.run_docs_mode(config, robots=robots))
        assert BASE + "/private/internal" not in fetched
        assert BASE + "/guide/start" in fetched
        assert BASE + "/guide/api" in fetched
        assert saved == 3

    def test_docs_requests_wait_on_limiter(self, monkeypatch, tmp_path):
        fetched: list[str] = []
        waited: list[str] = []

        async def fake_fetch(url, config, page_action=None, wait_time=None, user_data_dir=None):
            fetched.append(url)
            return FakeResponse(HOME if len(fetched) == 1 else "<html></html>")

        class Limiter:
            def __init__(self, interval):
                self.interval = interval

            async def wait(self, url):
                waited.append(url)

        monkeypatch.setattr(docs, "fetch_page_browser", fake_fetch)
        monkeypatch.setattr(docs, "HostRateLimiter", Limiter)
        config = ScrapeConfig(url=BASE + "/", output_root=str(tmp_path), docs=True, wait=0)
        asyncio.run(docs.run_docs_mode(config, robots=RobotsPolicy.allow_all(BASE)))
        assert waited == fetched[1:]
