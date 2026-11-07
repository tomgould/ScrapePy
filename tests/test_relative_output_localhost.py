"""A relative -o must give the same result as an absolute one.

Crawls a server bound to 127.0.0.1 from a working directory that is not the
output folder, with -o given as ./x, ../x/y, x/, ~/x and absolute forms.
"""

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scrapepy.config import ScrapeConfig
from scrapepy.scraper import run_scraper

WORDS = " ".join(["Readable words fill this paragraph so the page is long enough."] * 8)


def html(title, links=()):
    anchors = "".join(f'<p><a href="{h}">{h}</a></p>' for h in links)
    return (f"<html><head><title>{title}</title></head><body><article><h1>{title}</h1>"
            f"<p>{WORDS} {title}</p>{anchors}</article></body></html>")


ROUTES = {
    "/": ("text/html", html("Home", ["/sec/post-one", "/sec/post-two"])),
    "/sec": ("text/html", html("Section", ["/sec/post-one", "/sec/post-two"])),
    "/sec/post-one": ("text/html", html("Post one")),
    "/sec/post-two": ("text/html", html("Post two")),
    "/feed.xml": ("application/rss+xml", (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>F</title>'
        f"<item><title>Entry</title><link>/sec/post-one</link><description>{WORDS}"
        "</description></item></channel></rss>")),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = ROUTES.get(self.path.split("?")[0])
        if route is None:
            self.send_response(404)
            self.end_headers()
            return
        data = route[1].encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", route[0])
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    work = tmp_path / "work" / "cwd"
    work.mkdir(parents=True)
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return work


def crawl(url, out, **kw):
    kw.setdefault("wait", 0)
    kw.setdefault("concurrent", 1)
    kw.setdefault("include_assets", False)
    config = ScrapeConfig(url=url, output_root=out, **kw)
    code = asyncio.run(run_scraper(config))
    summary = json.loads((Path(config.output_dir) / "run_summary.json").read_text())
    return config, code, summary


@pytest.mark.parametrize("out,expected", [
    ("./x", "work/cwd/x"),
    ("../x/y", "work/x/y"),
    ("x/", "work/cwd/x"),
    ("~/x", "home/x"),
])
@pytest.mark.parametrize("extra", [
    {"markdown_only": True},
    {},
    {"markdown_only": True, "stay_in_path": True},
    {"stay_in_path": True},
], ids=["markdown-only", "default", "markdown-only-stay", "default-stay"])
def test_relative_output_dir(server, cwd, tmp_path, out, expected, extra):
    url = server + ("/sec/" if extra.get("stay_in_path") else "/")
    config, code, summary = crawl(url, out, **extra)
    assert code == 0, summary["failed"]
    assert summary["counts"]["failed"] == 0
    assert summary["counts"]["new"] >= 2
    assert summary["output_dir"] == str(tmp_path / expected)
    root = tmp_path / expected
    # Paths in the summary stay relative to the output folder
    for rel in summary["new_markdown"]:
        assert not os.path.isabs(rel)
        assert (root / rel).is_file()
    assert summary["markdown_dir"].startswith("MarkdownSites/")


def test_switching_between_relative_and_absolute_changes_nothing(server, cwd, tmp_path):
    absolute = str(tmp_path / "work" / "cwd" / "data")
    _, _, first = crawl(server + "/", "data")
    assert first["counts"]["failed"] == 0 and first["counts"]["new"] >= 2
    cache_path = Path(absolute) / "ScrapedSites" / first["html_dir"].split("/", 1)[1] / ".scrapepy_cache.json"
    cache_before = json.loads(cache_path.read_text())
    for out in (absolute, "./data", "../cwd/data/"):
        _, code, again = crawl(server + "/", out)
        assert code == 0
        counts = again["counts"]
        assert counts["new"] == 0 and counts["changed"] == 0 and counts["failed"] == 0
        assert counts["unchanged"] == counts["fetched"]
        assert again["new_markdown"] == [] and again["changed_markdown"] == []
        cache_after = json.loads(cache_path.read_text())
        assert cache_after.get("assignments") == cache_before.get("assignments")
        assert sorted(cache_after["pages"]) == sorted(cache_before["pages"])


def test_feed_with_relative_output_dir(server, cwd, tmp_path):
    config, code, summary = crawl(server + "/feed.xml", "../feeds")
    assert code == 0
    assert summary["feed"]["entries_written"] == 1
    assert summary["output_dir"] == str(tmp_path / "work" / "feeds")
    (rel,) = summary["new_markdown"]
    assert not os.path.isabs(rel)
    assert (tmp_path / "work" / "feeds" / rel).is_file()
    # A second run with the absolute form finds the entry unchanged
    _, code, again = crawl(server + "/feed.xml", str(tmp_path / "work" / "feeds"))
    assert again["feed"]["entries_unchanged"] == 1 and again["new_markdown"] == []


def test_config_output_paths_are_absolute(cwd, tmp_path):
    config = ScrapeConfig(url="https://example.com/", output_root="rel")
    assert config.output_root == str(tmp_path / "work" / "cwd" / "rel")
    assert os.path.isabs(config.output_dir) and os.path.isabs(config.markdown_dir)
    home = ScrapeConfig(url="https://example.com/", output_root="~/crawls")
    assert home.output_root == str(tmp_path / "home" / "crawls")
