"""A crawl and feed mode writing into the same site folder, from a local
test server (127.0.0.1 only): neither ever replaces the other's file."""

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from scrapepy.config import ScrapeConfig
from scrapepy.modes import crawl
from scrapepy.output.naming import url_suffix
from scrapepy.output.writer import create_output_dirs
from scrapepy.scraper import run_scraper

WORDS = "Plenty of readable words appear here so the page passes the length check. " * 6
HTML = "text/html; charset=utf-8"


def article(title, token):
    return (f"<html><head><title>{title}</title></head><body><article><h1>{title}</h1>"
            f"<p>{WORDS}</p><p>{token}</p></article></body></html>")


def home(links):
    anchors = "".join(f'<p><a href="{h}">{h}</a></p>' for h in links)
    return (f"<html><head><title>Home</title></head><body><main><h1>Home</h1><p>{WORDS}</p>"
            f"{anchors}</main></body></html>")


def rss(entries):
    items = "".join(f"<item><title>{t}</title><link>{link}</link>"
                    f"<description>{body} with enough words to keep.</description></item>"
                    for link, t, body in entries)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>F</title>{items}</channel></rss>'


def atom(entries):
    items = "".join(f'<entry><title>{t}</title><link href="{link}"/><id>{link}</id>'
                    f"<content>{body} with enough words to keep.</content></entry>"
                    for link, t, body in entries)
    return f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>F</title>{items}</feed>'


FEEDS = {"rss": ("application/rss+xml", rss), "atom": ("application/atom+xml", atom)}


class Handler(BaseHTTPRequestHandler):
    routes: dict = {}

    def do_GET(self):
        route = Handler.routes.get(self.path.split("?")[0])
        if route is None:
            self.send_response(404)
            self.end_headers()
            return
        ctype, body = route
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    Handler.routes = {}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def site(links, feed_kind, entries, base):
    ctype, build = FEEDS[feed_kind]
    Handler.routes = {"/": (HTML, home(links)),
                      "/plain": (HTML, article("Plain page", "PLAINBODY")),
                      "/about": (HTML, article("About us", "ABOUTBODY")),
                      "/feed": (ctype, build([(base + link, t, b) for link, t, b in entries]))}


def do_crawl(base, out, markdown_only):
    config = ScrapeConfig(url=base + "/", output_root=str(out), markdown_only=markdown_only,
                          wait=0, concurrent=1)
    create_output_dirs(config)
    return config, asyncio.run(crawl.run_crawl_mode(config))


def do_feed(base, out):
    config = ScrapeConfig(url=base + "/feed", output_root=str(out), wait=0, feed=True)
    asyncio.run(run_scraper(config))
    return json.loads((Path(config.output_dir) / "run_summary.json").read_text())


def files(config):
    out = {}
    for d, _, names in os.walk(config.markdown_dir):
        for n in names:
            p = os.path.join(d, n)
            text = open(p, encoding="utf-8").read()
            front = yaml.safe_load(text.split("\n---\n", 1)[0].removeprefix("---\n"))
            out[os.path.relpath(p, config.markdown_dir)] = (front["url"], text)
    return out


def by_url(config):
    return {u: f for f, (u, _) in files(config).items()}


@pytest.mark.parametrize("markdown_only", [True, False], ids=["md-only", "default"])
@pytest.mark.parametrize("feed_kind", ["rss", "atom"])
class TestFeedThenCrawl:
    def test_crawl_never_takes_a_feed_entry_file(self, server, tmp_path, feed_kind, markdown_only):
        entry = [("/about/", "Post", "FEEDBODY")]
        site(["/plain"], feed_kind, entry, server)
        do_crawl(server, tmp_path, markdown_only)
        do_feed(server, tmp_path)
        # The feed moves on, then the crawl meets /about
        site(["/plain", "/about"], feed_kind, [("/other/", "Other", "OTHERBODY")], server)
        do_feed(server, tmp_path)
        config, summary = do_crawl(server, tmp_path, markdown_only)
        mapping = by_url(config)
        feed_file = mapping[server + "/about/"]
        assert "FEEDBODY" in files(config)[feed_file][1]
        assert mapping[server + "/about"] != feed_file
        assert "ABOUTBODY" in files(config)[mapping[server + "/about"]][1]
        # /about/ (feed) and /about (crawl) are different URLs: the feed entry
        # keeps about.md, the crawled page gets the hash-suffixed name
        assert feed_file == "about.md"
        assert mapping[server + "/about"] == f"about-{url_suffix(server + '/about')}.md"
        assert [r["url"] for r in summary["renamed"]] == [server + "/about"]
        # Second crawl: nothing moves, nothing is written again
        config, again = do_crawl(server, tmp_path, markdown_only)
        assert by_url(config) == mapping
        assert again["counts"]["changed"] == 0 and again["counts"]["new"] == 0

    def test_same_url_in_feed_and_crawl_keeps_its_file(self, server, tmp_path, feed_kind, markdown_only):
        site(["/plain"], feed_kind, [("/about", "About", "FEEDBODY")], server)
        do_crawl(server, tmp_path, markdown_only)
        do_feed(server, tmp_path)
        feed_file = by_url(ScrapeConfig(url=server + "/", output_root=str(tmp_path)))[server + "/about"]
        site(["/plain", "/about"], feed_kind, [], server)
        config, summary = do_crawl(server, tmp_path, markdown_only)
        # The crawl writes the same URL: it takes its own file back, unsuffixed
        assert by_url(config)[server + "/about"] == feed_file
        assert summary["renamed"] == []


@pytest.mark.parametrize("markdown_only", [True, False], ids=["md-only", "default"])
@pytest.mark.parametrize("feed_kind", ["rss", "atom"])
def test_crawl_then_feed(server, tmp_path, feed_kind, markdown_only):
    site(["/plain", "/about"], feed_kind, [("/about/", "Post", "FEEDBODY")], server)
    config, _ = do_crawl(server, tmp_path, markdown_only)
    crawled = by_url(config)[server + "/about"]
    summary = do_feed(server, tmp_path)
    mapping = by_url(config)
    assert mapping[server + "/about"] == crawled
    assert mapping[server + "/about/"] != crawled
    assert "FEEDBODY" in files(config)[mapping[server + "/about/"]][1]
    assert crawled == "about.md"
    assert mapping[server + "/about/"] == f"about-{url_suffix(server + '/about/')}.md"
    assert [r["url"] for r in summary["renamed"]] == [server + "/about/"]
    # Both again: stable
    do_feed(server, tmp_path)
    config, again = do_crawl(server, tmp_path, markdown_only)
    assert by_url(config) == mapping
    assert again["counts"]["changed"] == 0


@pytest.mark.parametrize("front", [
    "---\ntitle: Hand edited\n---\n",
    "---\ntitle: Hand edited\nurl: 123\n---\n",
    "---\ntitle: Hand edited\nurl: [not, a, url]\n---\n",
    "---\nurl: not a url\n---\n",
    "---\nurl: \"unclosed\n---\n",
    "no frontmatter at all\n",
])
def test_hand_edited_file_is_unowned(server, tmp_path, front):
    site(["/plain", "/about"], "rss", [], server)
    config, _ = do_crawl(server, tmp_path, True)
    path = os.path.join(config.markdown_dir, by_url(config)[server + "/about"])
    Path(path).write_text(front + "\nSome hand written text.\n", encoding="utf-8")
    cache = os.path.join(config.output_dir, ".scrapepy_cache.json")
    os.remove(cache)
    config, summary = do_crawl(server, tmp_path, True)
    assert summary["failed"] == []
    assert os.path.join(config.markdown_dir, by_url(config)[server + "/about"]) == path
