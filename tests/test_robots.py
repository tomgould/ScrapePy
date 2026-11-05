"""Tests for robots.txt rules (RFC 9309), Crawl-delay and per-host pacing (no network)."""

import asyncio

import pytest

from scrapepy.config import ScrapeConfig
from scrapepy.processing import discovery
from scrapepy.processing.robots import (
    DEFAULT_ROBOTS_AGENT,
    HostRateLimiter,
    RobotsPolicy,
    normalise_path,
    parse_robots,
    policy_from_response,
    product_token,
    robots_url,
)

BASE = "https://example.com"


def allowed(rules: str, path: str, ua: str | None = None) -> bool:
    return RobotsPolicy.from_text(rules, user_agent=ua, base_url=BASE).can_fetch(path)


def both_orders(*lines: str) -> list[str]:
    """The rules as a * group, in the given order and reversed."""
    return [
        "User-agent: *\n" + "\n".join(lines) + "\n",
        "User-agent: *\n" + "\n".join(reversed(lines)) + "\n",
    ]


class TestPrecedence:
    """Longest match wins, Allow wins a tie, order in the file does not matter."""

    @pytest.mark.parametrize("rules", both_orders("Allow: /docs/", "Disallow: /docs/secret"))
    def test_longer_disallow_beats_shorter_allow(self, rules):
        assert not allowed(rules, "/docs/secret.html")
        assert allowed(rules, "/docs/public.html")

    @pytest.mark.parametrize("rules", both_orders("Disallow: /a/", "Allow: /a/b/"))
    def test_longer_allow_beats_shorter_disallow(self, rules):
        assert allowed(rules, "/a/b/c")
        assert not allowed(rules, "/a/c")

    @pytest.mark.parametrize("rules", both_orders("Allow: /page", "Disallow: /page"))
    def test_allow_wins_a_tie(self, rules):
        assert allowed(rules, "/page")

    @pytest.mark.parametrize("rules", both_orders("Allow: /docs/", "Disallow: /docs/secret", "Disallow: /*d.html$"))
    def test_reviewer_case(self, rules):
        assert not allowed(rules, "/d.html")
        assert not allowed(rules, "/docs/secret.html")
        assert not allowed(rules, "/docs/old.html")  # /*d.html$ is longer than /docs/
        assert allowed(rules, "/docs/index.html")

    @pytest.mark.parametrize("rules", both_orders("Allow: /$", "Disallow: /"))
    def test_only_root_allowed(self, rules):
        assert allowed(rules, "/")
        assert not allowed(rules, "/page")

    def test_no_matching_rule_allows(self):
        assert allowed("User-agent: *\nDisallow: /private\n", "/public")


class TestWildcards:
    @pytest.mark.parametrize("rules", both_orders("Disallow: /*?sort=", "Allow: /list"))
    def test_star_in_query(self, rules):
        assert not allowed(rules, "/list?sort=asc")
        assert allowed(rules, "/list?page=2")

    @pytest.mark.parametrize("rules", both_orders("Disallow: /*.pdf$", "Allow: /fi"))
    def test_dollar_anchors_end(self, rules):
        assert not allowed(rules, "/files/a.pdf")
        assert allowed(rules, "/files/a.pdfx")
        assert allowed(rules, "/files/a.pdf?x=1") is True

    def test_star_matches_empty_run(self):
        assert not allowed("User-agent: *\nDisallow: /a*b\n", "/ab")
        assert not allowed("User-agent: *\nDisallow: /a*b\n", "/axxxb/c")
        assert allowed("User-agent: *\nDisallow: /a*b\n", "/ac")

    def test_trailing_star_is_same_as_prefix(self):
        assert not allowed("User-agent: *\nDisallow: /tmp*\n", "/tmp/x")

    def test_dollar_without_star(self):
        rules = "User-agent: *\nDisallow: /exact$\n"
        assert not allowed(rules, "/exact")
        assert allowed(rules, "/exact/more")

    def test_regex_characters_are_literal(self):
        rules = "User-agent: *\nDisallow: /a.b+(c)\n"
        assert not allowed(rules, "/a.b+(c)")
        assert allowed(rules, "/axb+(c)")


class TestPaths:
    def test_case_sensitive(self):
        rules = "User-agent: *\nDisallow: /Admin\n"
        assert allowed(rules, "/admin")
        assert not allowed(rules, "/Admin/x")

    def test_query_string_prefix(self):
        assert not allowed("User-agent: *\nDisallow: /search?q=\n", "/search?q=x")

    def test_directory_rule_does_not_match_bare_name(self):
        assert allowed("User-agent: *\nDisallow: /tmp/\n", "/tmp")

    def test_percent_encoded_pattern_matches_utf8_path(self):
        assert not allowed("User-agent: *\nDisallow: /caf%C3%A9\n", "/café")

    def test_utf8_pattern_matches_encoded_path(self):
        assert not allowed("User-agent: *\nDisallow: /café\n", "/caf%c3%a9")

    def test_encoded_unreserved_characters_match(self):
        assert not allowed("User-agent: *\nDisallow: /%7Euser\n", "/~user/page")

    def test_normalise_path(self):
        assert normalise_path("/caf%c3%a9") == "/caf%C3%A9"
        assert normalise_path("/café") == "/caf%C3%A9"
        assert normalise_path("/%41b") == "/Ab"
        assert normalise_path("/a%2Fb") == "/a%2Fb"

    def test_full_url_and_relative_path(self):
        rules = "User-agent: *\nDisallow: /private/\n"
        assert not allowed(rules, BASE + "/private/x")
        assert not allowed(rules, "private/x")

    def test_robots_txt_always_allowed(self):
        assert allowed("User-agent: *\nDisallow: /\n", "/robots.txt")


class TestEmptyAndMissing:
    def test_empty_disallow_allows_everything(self):
        assert allowed("User-agent: *\nDisallow:\n", "/anything")

    def test_empty_disallow_next_to_rules(self):
        assert not allowed("User-agent: *\nDisallow:\nDisallow: /x\n", "/x")

    def test_empty_file_allows_everything(self):
        policy = RobotsPolicy.from_text("", base_url=BASE)
        assert policy.can_fetch("/x")
        assert policy.crawl_delay is None

    def test_no_robots_means_allowed(self):
        policy = RobotsPolicy.from_text(None, base_url=BASE)
        assert not policy.has_rules
        assert policy.can_fetch("/private/page")

    def test_comments_and_blank_lines(self):
        rules = "# hello\nUser-agent: * # all\n\nDisallow: /x # no\n"
        assert not allowed(rules, "/x")

    def test_rules_before_any_user_agent_are_ignored(self):
        assert allowed("Disallow: /\nUser-agent: *\nDisallow: /x\n", "/page")


GROUPS = """\
User-agent: *
Disallow: /
Crawl-delay: 1

User-agent: MyBot
Disallow: /private
Crawl-delay: 7

User-agent: MyBot-News
Disallow: /news-private
"""


class TestUserAgentGroups:
    def test_named_group_replaces_star(self):
        assert allowed(GROUPS, "/public", ua="MyBot/1.0")
        assert not allowed(GROUPS, "/private/x", ua="MyBot/1.0")

    def test_default_agent_uses_star_group(self):
        assert not allowed(GROUPS, "/public")

    def test_unlisted_agent_uses_star_group(self):
        assert not allowed(GROUPS, "/public", ua="OtherBot/2.0")

    def test_agent_match_is_case_insensitive(self):
        assert allowed(GROUPS, "/public", ua="mybot")

    def test_longest_agent_name_wins(self):
        assert allowed(GROUPS, "/private", ua="MyBot-News/3.0")
        assert not allowed(GROUPS, "/news-private", ua="MyBot-News/3.0")

    def test_groups_for_same_agent_are_merged(self):
        rules = "User-agent: a\nDisallow: /x\n\nUser-agent: b\nDisallow: /y\n\nUser-agent: a\nDisallow: /z\n"
        assert not allowed(rules, "/x", ua="a")
        assert not allowed(rules, "/z", ua="a")
        assert allowed(rules, "/y", ua="a")

    def test_multiple_agents_share_a_group(self):
        rules = "User-agent: a\nUser-agent: b\nDisallow: /x\n"
        assert not allowed(rules, "/x", ua="b")

    def test_product_token(self):
        assert product_token("MyBot/1.0 (+https://example.org)") == "mybot"
        assert product_token(None) == DEFAULT_ROBOTS_AGENT
        assert product_token("  ") == DEFAULT_ROBOTS_AGENT

    def test_parse_groups(self):
        groups = parse_robots(GROUPS)
        assert [g.agents for g in groups] == [["*"], ["mybot"], ["mybot-news"]]


class TestCrawlDelay:
    def test_star_group_delay(self):
        assert RobotsPolicy.from_text(GROUPS, base_url=BASE).crawl_delay == 1.0

    def test_named_group_delay(self):
        assert RobotsPolicy.from_text(GROUPS, user_agent="MyBot", base_url=BASE).crawl_delay == 7.0

    def test_delay_not_inherited_from_star(self):
        policy = RobotsPolicy.from_text(GROUPS, user_agent="MyBot-News", base_url=BASE)
        assert policy.crawl_delay is None

    def test_fractional_delay(self):
        policy = RobotsPolicy.from_text("User-agent: *\nCrawl-delay: 2.5\n", base_url=BASE)
        assert policy.crawl_delay == 2.5
        assert policy.min_delay(1.0) == 2.5

    def test_invalid_delay_is_ignored(self):
        for value in ("soon", "-3", "nan", "inf"):
            policy = RobotsPolicy.from_text(f"User-agent: *\nCrawl-delay: {value}\n", base_url=BASE)
            assert policy.crawl_delay is None, value

    def test_delay_raises_wait_time_but_never_lowers_it(self):
        policy = RobotsPolicy.from_text("User-agent: *\nCrawl-delay: 5\n", base_url=BASE)
        assert policy.min_delay(3.0) == 5.0
        assert policy.min_delay(8.0) == 8.0

    def test_no_delay(self):
        assert RobotsPolicy.from_text("User-agent: *\nDisallow: /x\n").min_delay(3.0) == 3.0


class TestIgnoreRobots:
    def test_not_enforced_allows_disallowed(self):
        policy = RobotsPolicy.from_text("User-agent: *\nDisallow: /\nCrawl-delay: 9\n",
                                        base_url=BASE, enforced=False)
        assert policy.can_fetch("/private/page")
        assert policy.min_delay(3.0) == 3.0

    def test_allow_all_is_not_enforced(self):
        policy = RobotsPolicy.allow_all(BASE)
        assert not policy.enforced
        assert policy.can_fetch("/anything")


class TestFetchStatus:
    """RFC 9309 2.3.1: 4xx means no restrictions, 5xx or unreachable means disallow all."""

    def test_200_parses(self):
        policy = policy_from_response(200, "User-agent: *\nDisallow: /x\n", base_url=BASE)
        assert not policy.can_fetch("/x")
        assert policy.can_fetch("/y")

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 451])
    def test_4xx_allows_everything(self, status):
        policy = policy_from_response(status, None, base_url=BASE)
        assert policy.can_fetch("/anything")
        assert not policy.disallow_all

    @pytest.mark.parametrize("status", [500, 502, 503, 0])
    def test_5xx_and_unreachable_disallow_everything(self, status):
        policy = policy_from_response(status, None, base_url=BASE)
        assert policy.disallow_all
        assert not policy.can_fetch("/")
        assert not policy.can_fetch("/page")
        assert "could not be read" in policy.note
        assert ("unreachable" in policy.note) == (status == 0)

    def test_ignore_robots_overrides_5xx(self):
        policy = policy_from_response(503, None, base_url=BASE, enforced=False)
        assert policy.can_fetch("/page")


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
        assert HostRateLimiter(3.0, clock=FakeClock()).reserve("example.com") == 0

    def test_burst_is_spaced(self):
        limiter = HostRateLimiter(3.0, clock=FakeClock())
        assert [limiter.reserve("example.com") for _ in range(4)] == [0, 3.0, 6.0, 9.0]

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

    def test_shared_state_across_intervals(self):
        """The first page request waits the page gap after the last discovery request."""
        clock = FakeClock()
        limiter = HostRateLimiter(2.0, clock=clock)
        assert limiter.reserve("h", 1.0) == 0
        assert limiter.reserve("h", 1.0) == 1.0
        assert limiter.reserve("h") == 3.0  # 1.0 (last discovery slot) + 2.0 page gap

    def test_view_shares_state(self):
        clock = FakeClock()
        limiter = HostRateLimiter(2.0, clock=clock, sleep=clock.sleep)
        view = limiter.with_interval(0.5)

        async def go():
            await view.wait(BASE + "/sitemap.xml")
            await limiter.wait(BASE + "/")

        asyncio.run(go())
        assert clock.sleeps == [2.0]
        assert view.min_interval == 0.5

    def test_concurrent_waits_are_spaced(self):
        clock = FakeClock()
        limiter = HostRateLimiter(2.0, clock=clock, sleep=clock.sleep)

        async def burst():
            await asyncio.gather(*[limiter.wait(BASE + f"/p{i}") for i in range(4)])

        asyncio.run(burst())
        assert sorted(clock.sleeps) == [2.0, 4.0, 6.0]


class FakeResponse:
    def __init__(self, status, body=b""):
        self.status = status
        self.body = body
        self.encoding = "utf-8"


class TestLoadRobotsPolicy:
    def _patch(self, monkeypatch, response):
        def fake(url, config, headers=None):
            if isinstance(response, Exception):
                raise response
            return response
        monkeypatch.setattr(discovery, "fetch_page_http", fake)

    def test_uses_fetched_content_and_user_agent(self, monkeypatch):
        self._patch(monkeypatch, FakeResponse(200, GROUPS.encode()))
        policy, content = discovery.load_robots_policy(ScrapeConfig(url=BASE, user_agent="MyBot/1.0"))
        assert content == GROUPS
        assert policy.can_fetch("/public")
        assert not policy.can_fetch("/private")

    def test_404_allows_all(self, monkeypatch):
        self._patch(monkeypatch, FakeResponse(404))
        policy, content = discovery.load_robots_policy(ScrapeConfig(url=BASE))
        assert content is None
        assert policy.can_fetch("/private/a")

    def test_503_disallows_all(self, monkeypatch):
        self._patch(monkeypatch, FakeResponse(503))
        policy, _ = discovery.load_robots_policy(ScrapeConfig(url=BASE))
        assert not policy.can_fetch("/")

    def test_unreachable_disallows_all(self, monkeypatch):
        self._patch(monkeypatch, ConnectionError("refused"))
        policy, content = discovery.load_robots_policy(ScrapeConfig(url=BASE))
        assert content is None
        assert not policy.can_fetch("/")
        assert "unreachable" in policy.note

    def test_ignore_robots_keeps_content_for_sitemaps(self, monkeypatch):
        self._patch(monkeypatch, FakeResponse(200, GROUPS.encode()))
        policy, content = discovery.load_robots_policy(ScrapeConfig(url=BASE, ignore_robots=True))
        assert content == GROUPS
        assert policy.can_fetch("/public")


class TestDiscoveryRespectsRobots:
    def test_disallowed_discovery_urls_are_not_fetched(self, monkeypatch):
        fetched: list[str] = []

        def fake_fetch(url, config):
            fetched.append(url)
            return None

        monkeypatch.setattr(discovery, "_fetch_url", fake_fetch)
        policy = RobotsPolicy.from_text("User-agent: *\nDisallow: /feed\nDisallow: /rss\n", base_url=BASE)
        asyncio.run(discovery.run_discovery(ScrapeConfig(url=BASE), robots=policy))
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
