"""Network-safety options without a network: the private-address check,
the hop-by-hop redirect loop, the robots.txt size and Sitemap caps, the
sitemap host rule, --no-cloudflare-solve and how the options are recorded."""

import asyncio
import socket

import pytest

from scrapepy.cli import parse_args
from scrapepy.config import ScrapeConfig
from scrapepy.engine import fetcher, redirects
from scrapepy.engine.redirects import (
    RedirectRefused, check_redirect, is_private_address, private_redirect_reason,
)
from scrapepy.output.run_summary import options_used
from scrapepy.processing import discovery

SITE = "http://example.com/start"


@pytest.mark.parametrize("address", [
    "127.0.0.1", "127.255.255.254", "0.0.0.0", "10.0.0.1", "10.255.255.255",
    "172.16.0.1", "172.31.255.255", "192.168.1.1", "169.254.169.254", "169.254.0.1",
    "100.64.0.1", "100.127.255.255", "::1", "::", "fc00::1", "fd12:3456::1",
    "fe80::1", "fe80::1%eth0", "::ffff:127.0.0.1", "::ffff:169.254.169.254",
    "::ffff:10.1.2.3", "::ffff:100.64.0.9",
    # IPv4 inside IPv6: IPv4-compatible, NAT64, 6to4
    "::127.0.0.1", "::169.254.169.254", "64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe",
    "2002:7f00:1::", "2002:a00:1::1",
    # benchmarking, reserved, multicast, broadcast
    "198.18.0.1", "198.19.255.255", "240.0.0.1", "255.255.255.255", "224.0.0.1",
    "239.255.255.250", "ff02::1", "ff05::2",
    # documentation and protocol ranges, known only to ipaddress
    "192.0.2.1", "203.0.113.5", "192.0.0.8", "2001:db8::1",
])
def test_private_addresses(address):
    assert is_private_address(address)


@pytest.mark.parametrize("address", [
    "8.8.8.8", "1.1.1.1", "172.15.255.255", "172.32.0.1", "100.63.255.255",
    "100.128.0.1", "169.253.255.255", "192.169.0.1", "11.0.0.1", "2001:4860:4860::8888",
    "::ffff:8.8.8.8", "fec0::1", "example.com", "", "not an address",
    "64:ff9b::808:808", "2002:808:808::", "::8.8.8.8", "198.17.255.255", "198.20.0.1",
    "223.255.255.254",
])
def test_public_addresses_and_names(address):
    assert not is_private_address(address)


def _resolver(*addresses):
    calls = []

    def resolve(host, port):
        calls.append((host, port))
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 (a, port)) for a in addresses]
    resolve.calls = calls
    return resolve


def test_a_name_resolving_to_loopback_is_refused():
    reason = private_redirect_reason("http://intranet.example:9/x", SITE,
                                     resolve=_resolver("127.0.0.1"))
    assert reason and "intranet.example resolves to 127.0.0.1" in reason


def test_any_private_resolved_address_refuses():
    resolve = _resolver("93.184.216.34", "10.0.0.7")
    reason = private_redirect_reason("https://mixed.example.net/", SITE, resolve)
    assert reason == "redirect to a private address refused (mixed.example.net resolves to 10.0.0.7)"
    assert resolve.calls == [("mixed.example.net", 443)]


def test_public_resolution_passes():
    resolve = _resolver("93.184.216.34", "2606:2800:220:1::1")
    assert private_redirect_reason("http://public.example.net:8080/", SITE, resolve) is None
    assert resolve.calls == [("public.example.net", 8080)]


def test_unresolvable_name_is_left_to_the_fetch():
    def resolve(host, port):
        raise socket.gaierror("no such name")
    assert private_redirect_reason("http://nowhere.invalid/", SITE, resolve) is None


@pytest.mark.parametrize("target", [
    "http://127.0.0.1:8000/", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.1/",
    "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://[fe80::1]/", "http://100.64.1.1/",
    "http://0.0.0.0/",
])
def test_private_literals_refused_without_resolving(target):
    resolve = _resolver()
    assert "private address" in private_redirect_reason(target, SITE, resolve)
    assert resolve.calls == []


def test_public_literal_passes_without_resolving():
    resolve = _resolver("10.0.0.1")
    assert private_redirect_reason("http://93.184.216.34/", SITE, resolve) is None
    assert resolve.calls == []


def test_same_host_and_port_as_the_start_url_passes():
    site = "http://127.0.0.1:8000/"
    assert private_redirect_reason("http://127.0.0.1:8000/other", site) is None
    assert private_redirect_reason("http://127.0.0.1:8001/other", site) is not None
    assert private_redirect_reason("http://localhost:8000/other", site) is not None
    # The default ports count as the same port
    assert private_redirect_reason("http://localhost:80/x", "http://LOCALHOST/") is None


@pytest.mark.parametrize("target", ["file:///etc/passwd", "gopher://example.com/", "ftp://example.com/"])
def test_non_http_targets_refused(target):
    assert "non-HTTP" in private_redirect_reason(target, SITE, _resolver())


def test_bad_port_and_missing_host_refused():
    assert "invalid URL" in private_redirect_reason("http://example.com:99999/", SITE)
    assert "without a host" in private_redirect_reason("http:///path", SITE)


def test_check_redirect_resolves_relative_locations():
    assert check_redirect("http://example.com/a/b", "../c", SITE, _resolver("93.184.216.34")) \
        == "http://example.com/c"
    with pytest.raises(RedirectRefused, match="169.254.169.254"):
        check_redirect("http://example.com/a", "http://169.254.169.254/", SITE)


# -- fetch_page_http ---------------------------------------------------------

class FakeResponse:
    def __init__(self, status, location=None, url=""):
        self.status = status
        self.headers = {"Location": location} if location else {}
        self.url = url
        self.body = b""
        self.encoding = "utf-8"


class FakeFetcher:
    def __init__(self, chain):
        self.chain = chain      # url -> FakeResponse
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.chain[url]


def _patch_fetcher(monkeypatch, chain):
    fake = FakeFetcher(chain)
    monkeypatch.setattr(fetcher, "Fetcher", fake)
    monkeypatch.setattr(redirects.socket, "getaddrinfo",
                        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", port))])
    return fake


def test_default_hands_redirects_to_scrapling(monkeypatch):
    fake = _patch_fetcher(monkeypatch, {SITE: FakeResponse(200)})
    fetcher.fetch_page_http(SITE, ScrapeConfig(url=SITE))
    assert len(fake.calls) == 1
    assert fake.calls[0][1]["follow_redirects"] is True


def test_flag_follows_each_hop_itself(monkeypatch):
    fake = _patch_fetcher(monkeypatch, {
        SITE: FakeResponse(301, "/second"),
        "http://example.com/second": FakeResponse(302, "http://cdn.example.net/final"),
        "http://cdn.example.net/final": FakeResponse(200, url="http://cdn.example.net/final"),
    })
    config = ScrapeConfig(url=SITE, auth="user:pw", refuse_private_redirects=True)
    response = fetcher.fetch_page_http(SITE, config)
    assert response.status == 200
    assert [u for u, _ in fake.calls] == [SITE, "http://example.com/second", "http://cdn.example.net/final"]
    assert all(kw["follow_redirects"] is False for _, kw in fake.calls)
    # Authorization goes to the site's host only
    assert "Authorization" in fake.calls[1][1]["headers"]
    assert "Authorization" not in fake.calls[2][1]["headers"]


def test_flag_refuses_a_private_hop(monkeypatch):
    fake = _patch_fetcher(monkeypatch, {
        SITE: FakeResponse(302, "http://169.254.169.254/latest/meta-data/"),
    })
    with pytest.raises(RedirectRefused, match=r"private address refused \(169.254.169.254\)"):
        fetcher.fetch_page_http(SITE, ScrapeConfig(url=SITE, refuse_private_redirects=True))
    assert len(fake.calls) == 1


def test_flag_stops_after_the_hop_limit(monkeypatch):
    chain = {f"http://example.com/{i}": FakeResponse(302, f"/{i + 1}")
             for i in range(redirects.MAX_REDIRECTS + 2)}
    fake = _patch_fetcher(monkeypatch, chain)
    with pytest.raises(RedirectRefused, match="more than 10 redirects"):
        fetcher.fetch_page_http("http://example.com/0",
                                ScrapeConfig(url=SITE, refuse_private_redirects=True))
    assert len(fake.calls) == redirects.MAX_REDIRECTS + 1


def test_flag_returns_a_redirect_without_location(monkeypatch):
    _patch_fetcher(monkeypatch, {SITE: FakeResponse(302)})
    response = fetcher.fetch_page_http(SITE, ScrapeConfig(url=SITE, refuse_private_redirects=True))
    assert response.status == 302


def test_flag_leaves_an_explicit_no_follow_alone(monkeypatch):
    fake = _patch_fetcher(monkeypatch, {SITE: FakeResponse(302, "http://10.0.0.1/")})
    response = fetcher.fetch_page_http(SITE, ScrapeConfig(url=SITE, refuse_private_redirects=True),
                                       follow_redirects=False)
    assert response.status == 302 and len(fake.calls) == 1


def test_asset_client_hooks_only_with_the_flag():
    assert fetcher.redirect_hooks(ScrapeConfig(url=SITE)) == {}
    hooks = fetcher.redirect_hooks(ScrapeConfig(url=SITE, refuse_private_redirects=True))
    assert len(hooks["event_hooks"]["response"]) == 1


# -- --no-cloudflare-solve ---------------------------------------------------

@pytest.mark.parametrize("flags, expected", [([], True), (["--no-cloudflare-solve"], False)])
def test_cloudflare_solve_follows_the_flag(monkeypatch, flags, expected):
    seen = {}

    async def fake_fetch(url, **kwargs):
        seen.update(kwargs)
        return FakeResponse(200)
    monkeypatch.setattr(fetcher.StealthyFetcher, "async_fetch", fake_fetch)
    config = parse_args(["https://example.com/", "-j", *flags])
    asyncio.run(fetcher.fetch_page_browser(config.url, config))
    assert seen["solve_cloudflare"] is expected


# -- robots.txt caps and sitemap hosts ----------------------------------------

def test_robots_body_keeps_only_the_first_512_kb():
    head = b"User-agent: *\nSitemap: http://example.com/early.xml\n"
    padding = b"# " + b"x" * 1000 + b"\n"
    body = head + padding * 600 + b"Sitemap: http://example.com/late.xml\n"
    text = discovery.robots_body(body, "utf-8")
    assert len(text.encode()) <= discovery.ROBOTS_MAX_BYTES
    assert "early.xml" in text and "late.xml" not in text
    assert text.endswith("\n")


def test_robots_body_drops_a_cut_line():
    line = b"Sitemap: http://example.com/" + b"a" * 100 + b".xml\n"
    body = line * (discovery.ROBOTS_MAX_BYTES // len(line) + 5)
    text = discovery.robots_body(body, None)
    assert all(row.endswith(".xml") for row in text.splitlines())
    small = b"Sitemap: http://example.com/s.xml"   # no newline, under the cap: kept whole
    assert discovery.robots_body(small, "utf-8") == small.decode()


@pytest.mark.parametrize("url, allowed", [
    ("http://example.com/sitemap.xml", True),
    ("https://EXAMPLE.com:8443/sitemap.xml", True),
    ("http://www.example.com/sitemap.xml", True),
    ("http://WWW.example.com:8080/sitemap.xml", True),
    ("http://www2.example.com/sitemap.xml", False),
    ("http://www.www.example.com/sitemap.xml", False),
    ("http://wwwexample.com/sitemap.xml", False),
    ("http://cdn.example.net/sitemap.xml", False),
    ("http://other.example.org/sitemap.xml", True),
    ("ftp://example.com/sitemap.xml", False),
    ("/relative-sitemap.xml", False),
    ("http://example.com:bad/sitemap.xml", False),
    ("http:///sitemap.xml", False),
])
def test_sitemap_host_rule(url, allowed):
    config = ScrapeConfig(url="http://example.com/", allow_sitemap_hosts=["other.example.org"])
    assert discovery.sitemap_host_allowed(url, config) is allowed


@pytest.mark.parametrize("url, allowed", [
    ("http://example.com/sitemap.xml", True),
    ("https://www.example.com/sitemap.xml", True),
    ("http://cdn.example.com/sitemap.xml", False),
])
def test_sitemap_host_rule_for_a_www_site(url, allowed):
    config = ScrapeConfig(url="https://www.example.com/")
    assert discovery.sitemap_host_allowed(url, config) is allowed


def test_allow_sitemap_host_argument():
    config = parse_args(["http://example.com/", "--allow-sitemap-host", "CDN.Example.net",
                         "--allow-sitemap-host", "[2001:db8::1]"])
    assert config.allow_sitemap_hosts == ["cdn.example.net", "2001:db8::1"]
    assert parse_args(["http://example.com/"]).allow_sitemap_hosts == []


@pytest.mark.parametrize("bad", ["https://cdn.example.net", "cdn.example.net:8080",
                                 "cdn.example.net/x", "", "user@cdn.example.net"])
def test_allow_sitemap_host_rejects_urls_and_ports(bad):
    with pytest.raises(SystemExit) as e:
        parse_args(["http://example.com/", "--allow-sitemap-host", bad])
    assert e.value.code == 2


def test_options_recorded_only_when_set(tmp_path):
    default = options_used(parse_args(["http://example.com/", "-o", str(tmp_path)]))
    for key in ("allow_sitemap_hosts", "refuse_private_redirects", "solve_cloudflare"):
        assert key not in default
    opts = options_used(parse_args([
        "http://example.com/", "-o", str(tmp_path), "--allow-sitemap-host", "cdn.example.net",
        "--refuse-private-redirects", "--no-cloudflare-solve"]))
    assert opts["allow_sitemap_hosts"] == ["cdn.example.net"]
    assert opts["refuse_private_redirects"] is True
    assert opts["solve_cloudflare"] is False
    config = parse_args(["http://example.com/", "--refuse-private-redirects", "--no-cloudflare-solve"])
    assert config.refuse_private_redirects is True and config.solve_cloudflare is False


@pytest.mark.parametrize("target", [
    "http://localhost/x", "http://foo.localhost/x", "http://a.b.localhost:8080/x",
    "http://LOCALHOST./x", "http://app.localhost./x",
])
def test_localhost_names_refused_without_resolving(target):
    def resolve(host, port):
        raise AssertionError("resolved")
    reason = private_redirect_reason(target, SITE, resolve=resolve)
    assert reason and "local host name" in reason


def test_names_that_only_contain_localhost_are_resolved():
    seen = []

    def resolve(host, port):
        seen.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    assert private_redirect_reason("http://localhost.example.com/x", SITE, resolve=resolve) is None
    assert private_redirect_reason("http://mylocalhost/x", SITE, resolve=resolve) is None
    assert seen == ["localhost.example.com", "mylocalhost"]


@pytest.mark.parametrize("site, sitemap, allowed_hosts, allowed", [
    ("http://example.com/", "http://EXAMPLE.COM./sitemap.xml", [], True),
    ("http://example.com./", "http://example.com/sitemap.xml", [], True),
    ("http://example.com/", "http://cdn.example.net/s.xml", ["CDN.EXAMPLE.NET."], True),
    ("http://example.com/", "http://x.com/s.xml", ["X.COM."], True),
    ("http://b\u00fccher.example/", "http://xn--bcher-kva.example/s.xml", [], True),
    ("http://xn--bcher-kva.example/", "http://www.b\u00fccher.example/s.xml", [], True),
    ("http://example.com/", "http://b\u00fccher.example/s.xml", ["xn--bcher-kva.example"], True),
    ("http://example.com/", "http://xn--bcher-kva.example/s.xml", ["B\u00dcCHER.example"], True),
    ("http://b\u00fccher.example/", "http://xn--bcher-kvb.example/s.xml", [], False),
    ("http://example.com/", "http://x.com.evil.example/s.xml", ["x.com"], False),
])
def test_sitemap_hosts_are_normalised(site, sitemap, allowed_hosts, allowed):
    config = ScrapeConfig(url=site, allow_sitemap_hosts=allowed_hosts)
    assert discovery.sitemap_host_allowed(sitemap, config) is allowed


def test_allow_sitemap_host_with_trailing_dot_from_the_command_line():
    config = parse_args(["http://x.com/", "--allow-sitemap-host", "CDN.Example.NET."])
    assert discovery.sitemap_host_allowed("http://cdn.example.net/s.xml", config)
