"""--proxy reaches every request, and its password never appears anywhere.

Two servers bound to 127.0.0.1: an origin site and a recording HTTP proxy.
With --proxy the site is addressed as http://origin.test:<port>/, a name
that does not resolve, so a request can only succeed through the proxy,
which maps that name to 127.0.0.1 and marks what it forwards.
"""

import asyncio
import http.client
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from scrapepy.cli import parse_args
from scrapepy.config import ScrapeConfig
from scrapepy.proxy import mask_proxy, redact, validate_proxy
from scrapepy.scraper import run_scraper

PASSWORD = "Pw-s3cret-9z"
USER = "crawler"
HOST = "origin.test"
WORDS = " ".join(["Readable words fill this paragraph so the page is long enough."] * 8)

PAGES = {
    "/": ("text/html", "<html><head><title>Home</title>"
                       '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
                       f"</head><body><article><h1>Home</h1><p>{WORDS}</p>"
                       '<img src="/pic.png" alt="pic"><p><a href="/about">About</a></p>'
                       "</article></body></html>"),
    "/about": ("text/html", f"<html><head><title>About</title></head><body><article>"
                            f"<h1>About</h1><p>{WORDS} about</p></article></body></html>"),
    "/pic.png": ("image/png", "\x89PNG fake"),
    "/sitemap.xml": ("application/xml", '<?xml version="1.0"?><urlset '
                     'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>/about</loc>'
                     "<lastmod>2026-01-01</lastmod></url></urlset>"),
    "/feed.xml": ("application/rss+xml", '<?xml version="1.0"?><rss version="2.0"><channel>'
                  f"<title>F</title><item><title>Entry</title><link>/about</link>"
                  f"<description>{WORDS}</description></item></channel></rss>"),
    "/robots.txt": ("text/plain", "User-agent: *\nAllow: /\n"),
}


class Origin(BaseHTTPRequestHandler):
    log: list = []        # (path, came through the proxy)

    def do_GET(self):
        path = self.path.split("?")[0]
        Origin.log.append((path, self.headers.get("X-Test-Proxy") == "1"))
        page = PAGES.get(path)
        if page is None:
            self.send_response(404)
            self.end_headers()
            return
        data = page[1].encode("latin-1")
        self.send_response(200)
        self.send_header("Content-Type", page[0])
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class Proxy(BaseHTTPRequestHandler):
    log: list = []        # (absolute URL, Proxy-Authorization sent)
    origin_port = 0

    def do_GET(self):
        Proxy.log.append((self.path, bool(self.headers.get("Proxy-Authorization"))))
        target = urlsplit(self.path)
        if target.hostname not in (HOST, "127.0.0.1"):
            self.send_response(502)
            self.end_headers()
            return
        conn = http.client.HTTPConnection("127.0.0.1", Proxy.origin_port, timeout=10)
        path = target.path + (f"?{target.query}" if target.query else "")
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("proxy-authorization", "proxy-connection", "connection")}
        headers["X-Test-Proxy"] = "1"
        conn.request("GET", path or "/", headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        conn.close()

    def log_message(self, *args):
        pass


def _serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture
def servers(monkeypatch):
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
    Origin.log, Proxy.log = [], []
    origin, proxy = _serve(Origin), _serve(Proxy)
    Proxy.origin_port = origin.server_address[1]
    yield origin.server_address[1], proxy.server_address[1]
    for httpd in (origin, proxy):
        httpd.shutdown()
        httpd.server_close()


def run(url, tmp_path, **kw):
    kw.setdefault("wait", 0)
    kw.setdefault("concurrent", 1)
    config = ScrapeConfig(url=url, output_root=str(tmp_path / "out"), **kw)
    code = asyncio.run(run_scraper(config))
    summary_path = Path(config.output_dir) / "run_summary.json"
    return config, code, summary_path


def everything_written(tmp_path) -> str:
    parts = []
    for path in (tmp_path / "out").rglob("*"):
        if path.is_file():
            parts.append(path.read_bytes().decode("utf-8", "replace"))
    return "\n".join(parts)


def assert_no_password(capfd, caplog, tmp_path):
    out, err = capfd.readouterr()
    for text in (out, err, caplog.text, everything_written(tmp_path)):
        assert PASSWORD not in text
        assert f"{USER}:" not in text


def test_every_crawl_request_goes_through_the_proxy(servers, tmp_path, capfd, caplog):
    origin_port, proxy_port = servers
    proxy = f"http://{USER}:{PASSWORD}@127.0.0.1:{proxy_port}"
    config, code, summary_path = run(f"http://{HOST}:{origin_port}/", tmp_path, proxy=proxy)
    summary = json.loads(summary_path.read_text())
    assert code == 0, summary["failed"]
    paths = {p for p, _ in Origin.log}
    # Page, robots.txt, sitemap, feed (reconnaissance) and asset requests
    for expected in ("/", "/about", "/robots.txt", "/sitemap.xml", "/feed.xml", "/pic.png"):
        assert expected in paths, (expected, sorted(paths))
    assert all(via for _, via in Origin.log), "a request reached the origin directly"
    assert all(auth for _, auth in Proxy.log), "a request went without proxy credentials"
    assert summary["options"]["proxy_used"] is True
    assert_no_password(capfd, caplog, tmp_path)


def test_markdown_only_crawl_and_feed_mode_use_the_proxy(servers, tmp_path, capfd, caplog):
    origin_port, proxy_port = servers
    proxy = f"http://{USER}:{PASSWORD}@127.0.0.1:{proxy_port}"
    _, code, _ = run(f"http://{HOST}:{origin_port}/", tmp_path / "a", proxy=proxy,
                     markdown_only=True)
    assert code == 0
    _, code, summary_path = run(f"http://{HOST}:{origin_port}/feed.xml", tmp_path / "b",
                                proxy=proxy)
    assert code == 0
    assert json.loads(summary_path.read_text())["feed"]["entries_written"] == 1
    assert Origin.log and all(via for _, via in Origin.log)
    assert_no_password(capfd, caplog, tmp_path)


def test_without_proxy_the_proxy_sees_nothing(servers, tmp_path):
    origin_port, _ = servers
    _, code, _ = run(f"http://127.0.0.1:{origin_port}/", tmp_path)
    assert code == 0
    assert Proxy.log == []
    assert Origin.log and not any(via for _, via in Origin.log)


def test_unreachable_proxy_never_shows_its_password(servers, tmp_path, capfd, caplog):
    origin_port, _ = servers
    # A port nothing listens on: every request fails with a connection error
    proxy = f"http://{USER}:{PASSWORD}@127.0.0.1:9/"
    config, code, summary_path = run(f"http://{HOST}:{origin_port}/", tmp_path, proxy=proxy)
    assert code == 1
    assert Origin.log == []
    assert_no_password(capfd, caplog, tmp_path)


@pytest.mark.skipif(os.environ.get("SCRAPEPY_BROWSER_TESTS") != "1",
                    reason="starts a browser: set SCRAPEPY_BROWSER_TESTS=1 with the browser "
                           "installed (scrapling install) to run it")
def test_js_render_uses_the_proxy(servers, tmp_path, capfd, caplog):
    origin_port, proxy_port = servers
    proxy = f"http://{USER}:{PASSWORD}@127.0.0.1:{proxy_port}"
    _, code, _ = run(f"http://{HOST}:{origin_port}/", tmp_path, proxy=proxy, js_render=True,
                     headless=True, markdown_only=True)
    assert code == 0
    assert "/" in {p for p, _ in Origin.log}
    assert all(via for _, via in Origin.log)
    assert_no_password(capfd, caplog, tmp_path)


@pytest.mark.parametrize("bad", ["ftp://proxy.example:21", "proxy.example:8080", "http://",
                                 f"gopher://{USER}:{PASSWORD}@proxy.example",
                                 "http://proxy.example:notaport"])
def test_invalid_proxy_is_refused_without_echoing_it(bad, capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args(["https://example.com", "--proxy", bad])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--proxy must be a URL" in err
    assert PASSWORD not in err
    with pytest.raises(ValueError):
        ScrapeConfig(url="https://example.com", proxy=bad)


@pytest.mark.parametrize("good", ["http://proxy.example:8080", "https://proxy.example",
                                  "socks5://127.0.0.1:1080", "socks5h://u:p@proxy.example:1080",
                                  "socks4://proxy.example:1080"])
def test_valid_proxy_schemes(good):
    assert validate_proxy(good) == good
    assert parse_args(["https://example.com", "--proxy", good]).proxy == good


def test_mask_and_redact():
    url = f"http://{USER}:{PASSWORD}@proxy.example:8080"
    assert mask_proxy(url) == "http://***@proxy.example:8080"
    assert mask_proxy("http://proxy.example:8080") == "http://proxy.example:8080"
    text = f"Failed to connect via {url} as {USER}:{PASSWORD} ({PASSWORD})"
    cleaned = redact(text, url)
    assert PASSWORD not in cleaned and f"{USER}:" not in cleaned
    assert "http://***@proxy.example:8080" in cleaned
    encoded = "http://crawler:p%40ss@proxy.example"
    assert "p@ss" not in redact("auth p@ss and p%40ss", encoded)


def test_errors_that_quote_the_proxy_url_are_redacted(servers, tmp_path, capfd, caplog,
                                                      monkeypatch):
    origin_port, proxy_port = servers
    proxy = f"http://{USER}:{PASSWORD}@127.0.0.1:{proxy_port}"
    import scrapepy.engine.fetcher as fetcher

    def failing_get(url, **kwargs):
        raise RuntimeError(f"could not reach {url} via {kwargs.get('proxy') or proxy} "
                           f"as {USER}:{PASSWORD}")
    monkeypatch.setattr(fetcher.Fetcher, "get", failing_get)
    _, code, summary_path = run(f"http://{HOST}:{origin_port}/", tmp_path, proxy=proxy)
    assert code == 1
    summary = json.loads(summary_path.read_text())
    assert summary["failed"] and "***" in summary["failed"][0]["reason"]
    assert_no_password(capfd, caplog, tmp_path)
