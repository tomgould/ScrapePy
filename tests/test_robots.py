"""Tests for robots.txt rules, Crawl-delay and per-host pacing (no network)."""

import asyncio

import pytest

from scrapepy.config import ScrapeConfig
from scrapepy.processing import discovery
from scrapepy.processing.robots import (
    DEFAULT_ROBOTS_AGENT,
    HostRateLimiter,
    RobotsPolicy,
    robots_url,
)

BASE = "https://example.com"

ROBOTS = """\
User-agent: *
Disallow: /private/
Disallow: /search
Crawl-delay: 5

User-agent: ScrapePyTest
Disallow: /
Crawl-delay: 12

Sitemap: https://example.com/sitemap.xml
"""


class TestRobotsPolicyRules:
    def test_allows_unlisted_path(self):
        policy = RobotsPolicy.from_text(ROBOTS, base_url=BASE)
        assert policy.can_fetch("/about")
        assert policy.can_fetch(BASE + "/about")

    def test_disallows_listed_prefix(self):
        policy = RobotsPolicy.from_text(ROBOTS, base_url=BASE)
        assert not policy.can_fetch("/private/page")
        assert not policy.can_fetch(BASE + "/private/")
        assert not policy.can_fetch("/search?q=x")

    def test_relative_path_resolves_against_base(self):
        policy = RobotsPolicy.from_text(ROBOTS, base_url=BASE)
        assert not policy.can_fetch("private/x")

    def test_default_agent_uses_catch_all_group(self):
        policy = RobotsPolicy.from_text(ROBOTS, base_url=BASE)
        assert policy.user_agent == DEFAULT_ROBOTS_AGENT
        assert policy.can_fetch("/")

    def test_named_user_agent_matches_its_group(self):
        policy = RobotsPolicy.from_text(ROBOTS, user_agent="ScrapePyTest/1.0 (+https://example.org)", base_url=BASE)
        assert not policy.can_fetch("/")
        assert not policy.can_fetch("/about")

    def test_unlisted_user_agent_falls_back_to_catch_all(self):
        policy = RobotsPolicy.from_text(ROBOTS, user_agent="OtherBot/2.0", base_url=BASE)
        assert policy.can_fetch("/about")
        assert not policy.can_fetch("/private/a")

    def test_disallow_all(self):
        policy = RobotsPolicy.from_text("User-agent: *\nDisallow: /\n", base_url=BASE)
        assert not policy.can_fetch("/")
        assert not policy.can_fetch("/anything")

    def test_empty_disallow_allows_everything(self):
        policy = RobotsPolicy.from_text("User-agent: *\nDisallow:\n", base_url=BASE)
        assert policy.can_fetch("/private/a")

    def test_empty_file_allows_everything(self):
        policy = RobotsPolicy.from_text("", base_url=BASE)
        assert policy.can_fetch("/x")
        assert policy.crawl_delay is None

    def test_allow_line_before_disallow(self):
        text = "User-agent: *\nAllow: /private/open\nDisallow: /private/\n"
        policy = RobotsPolicy.from_text(text, base_url=BASE)
        assert policy.can_fetch("/private/open")
        assert not policy.can_fetch("/private/closed")


class TestMissingRobots:
    def test_none_means_allowed(self):
        policy = RobotsPolicy.from_text(None, base_url=BASE)
        assert not policy.has_rules
        assert policy.can_fetch("/private/page")
        assert policy.crawl_delay is None

    def test_allow_all_is_not_enforced(self):
        policy = RobotsPolicy.allow_all(BASE)
        assert not policy.enforced
        assert policy.can_fetch("/anything")


class TestIgnoreRobots:
    def test_not_enforced_allows_disallowed(self):
        policy = RobotsPolicy.from_text(ROBOTS, base_url=BASE, enforced=False)
        assert policy.can_fetch("/private/page")

    def test_not_enforced_ignores_crawl_delay(self):
        policy = RobotsPolicy.from_text(ROBOTS, base_url=BASE, enforced=False)
        assert policy.min_delay(3.0) == 3.0


class TestCrawlDelay:
    def test_catch_all_delay(self):
        policy = RobotsPolicy.from_text(ROBOTS, base_url=BASE)
        assert policy.crawl_delay == 5.0

    def test_named_agent_delay(self):
        policy = RobotsPolicy.from_text(ROBOTS, user_agent="ScrapePyTest", base_url=BASE)
        assert policy.crawl_delay == 12.0

    def test_no_delay_set(self):
        policy = RobotsPolicy.from_text("User-agent: *\nDisallow: /x\n", base_url=BASE)
        assert policy.crawl_delay is None
        assert policy.min_delay(3.0) == 3.0

    def test_delay_raises_wait_time(self):
        policy = RobotsPolicy.from_text(ROBOTS, base_url=BASE)
        assert policy.min_delay(3.0) == 5.0

    def test_wait_time_above_delay_wins(self):
        policy = RobotsPolicy.from_text(ROBOTS, base_url=BASE)
        assert policy.min_delay(8.0) == 8.0

    def test_fractional_delay_rounds_up(self):
        policy = RobotsPolicy.from_text("User-agent: *\nCrawl-delay: 0.5\n", base_url=BASE)
        assert policy.crawl_delay == 1.0
        policy = RobotsPolicy.from_text("User-agent: *\ncrawl-delay: 2.25\n", base_url=BASE)
        assert policy.crawl_delay == 3.0

    def test_invalid_delay_is_ignored(self):
        policy = RobotsPolicy.from_text("User-agent: *\nCrawl-delay: soon\n", base_url=BASE)
        assert policy.crawl_delay is None


class TestRobotsUrl:
    def test_builds_root_robots(self):
        assert robots_url("https://example.com") == "https://example.com/robots.txt"
        assert robots_url("https://example.com/") == "https://example.com/robots.txt"


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)


class TestHostRateLimiter:
    def test_first_request_is_immediate(self):
        limiter = HostRateLimiter(3.0, clock=FakeClock())
        assert limiter.reserve("example.com") == 0

    def test_burst_is_spaced(self):
        clock = FakeClock()
        limiter = HostRateLimiter(3.0, clock=clock)
        waits = [limiter.reserve("example.com") for _ in range(4)]
        assert waits == [0, 3.0, 6.0, 9.0]

    def test_hosts_are_independent(self):
        limiter = HostRateLimiter(3.0, clock=FakeClock())
        assert limiter.reserve("a.example") == 0
        assert limiter.reserve("b.example") == 0
        assert limiter.reserve("a.example") == 3.0

    def test_elapsed_time_counts(self):
        clock = FakeClock()
        limiter = HostRateLimiter(3.0, clock=clock)
        limiter.reserve("example.com")
        clock.now += 10
        assert limiter.reserve("example.com") == 0

    def test_zero_interval_never_waits(self):
        limiter = HostRateLimiter(0, clock=FakeClock())
        assert [limiter.reserve("h") for _ in range(3)] == [0, 0, 0]

    def test_concurrent_waits_are_spaced(self):
        clock = FakeClock()
        limiter = HostRateLimiter(2.0, clock=clock, sleep=clock.sleep)

        async def burst():
            await asyncio.gather(*[limiter.wait(BASE + f"/p{i}") for i in range(4)])

        asyncio.run(burst())
        assert sorted(clock.sleeps) == [2.0, 4.0, 6.0]


class TestLoadRobotsPolicy:
    def test_uses_fetched_content_and_user_agent(self, monkeypatch):
        monkeypatch.setattr(discovery, "fetch_robots_txt", lambda config: ROBOTS)
        config = ScrapeConfig(url=BASE, user_agent="ScrapePyTest/1.0")
        policy, content = discovery.load_robots_policy(config)
        assert content == ROBOTS
        assert policy.enforced
        assert not policy.can_fetch("/")

    def test_unreachable_robots_allows_all(self, monkeypatch):
        monkeypatch.setattr(discovery, "fetch_robots_txt", lambda config: None)
        policy, content = discovery.load_robots_policy(ScrapeConfig(url=BASE))
        assert content is None
        assert policy.can_fetch("/private/a")

    def test_ignore_robots_keeps_content_for_sitemaps(self, monkeypatch):
        monkeypatch.setattr(discovery, "fetch_robots_txt", lambda config: ROBOTS)
        config = ScrapeConfig(url=BASE, ignore_robots=True)
        policy, content = discovery.load_robots_policy(config)
        assert content == ROBOTS
        assert not policy.enforced
        assert policy.can_fetch("/private/a")


class TestDiscoveryRespectsRobots:
    def test_disallowed_discovery_urls_are_not_fetched(self, monkeypatch):
        fetched: list[str] = []

        def fake_fetch(url, config):
            fetched.append(url)
            return None

        monkeypatch.setattr(discovery, "_fetch_url", fake_fetch)
        policy = RobotsPolicy.from_text(
            "User-agent: *\nDisallow: /feed\nDisallow: /rss\n", base_url=BASE,
        )
        config = ScrapeConfig(url=BASE)
        asyncio.run(discovery.run_discovery(config, robots=policy))
        assert BASE + "/sitemap.xml" in fetched
        assert BASE + "/" in fetched
        assert not any(u.startswith(BASE + "/feed") or u.startswith(BASE + "/rss") for u in fetched)
        assert BASE + "/atom.xml" in fetched

    def test_disallow_all_fetches_nothing(self, monkeypatch):
        fetched: list[str] = []
        monkeypatch.setattr(discovery, "_fetch_url", lambda url, config: fetched.append(url))
        policy = RobotsPolicy.from_text("User-agent: *\nDisallow: /\n", base_url=BASE)
        date_map, homepage, feeds = asyncio.run(
            discovery.run_discovery(ScrapeConfig(url=BASE), robots=policy)
        )
        assert fetched == []
        assert homepage is None
        assert date_map == {}
