"""Pages whose URLs map to one output file, crawled from a local test server
(127.0.0.1 only). The same page must keep the file in every run, whatever
the link order, concurrency or cache state."""

import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from scrapepy.config import ScrapeConfig
from scrapepy.modes import crawl
from scrapepy.output.writer import create_output_dirs

WORDS = "Plenty of readable words appear here so the page passes the length check. " * 6


def article(title, extra=""):
    return (f"<html><head><title>{title}</title></head><body><article><h1>{title}</h1>"
            f"<p>{WORDS}</p><p>{extra}</p></article></body></html>")


def home(links):
    anchors = "".join(f'<p><a href="{href}">{href}</a></p>' for href in links)
    return f"<html><head><title>Home</title></head><body><main><h1>Home</h1><p>{WORDS}</p>{anchors}</main></body></html>"


class Handler(BaseHTTPRequestHandler):
    pages: dict = {}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path not in Handler.pages:
            self.send_response(404)
            self.end_headers()
            return
        data = Handler.pages[path].encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    Handler.pages = {}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def run(base, out, markdown_only=True, **kw):
    kw.setdefault("concurrent", 1)
    config = ScrapeConfig(url=base + "/", output_root=str(out), markdown_only=markdown_only,
                          wait=0, **kw)
    create_output_dirs(config)
    return config, asyncio.run(crawl.run_crawl_mode(config))


def md_by_url(config):
    """url -> (relative path, body) for every Markdown file written."""
    found = {}
    for d, _, files in os.walk(config.markdown_dir):
        for f in files:
            path = os.path.join(d, f)
            text = open(path, encoding="utf-8").read()
            front = yaml.safe_load(text.split("\n---\n", 1)[0].removeprefix("---\n"))
            found[front["url"]] = (os.path.relpath(path, config.markdown_dir), text)
    return found


def check_story_run(base, config, summary, winner_text="Story"):
    files = md_by_url(config)
    assert base + "/story.html" not in files
    assert base + "/story" in files
    assert winner_text in files[base + "/story"][1]
    collided = {c["url"]: c for c in summary["collisions"]}
    assert collided[base + "/story.html"]["kept"] == base + "/story"
    assert base + "/story.html" not in summary["unchanged_urls"]
    assert base + "/story" not in [c["url"] for c in summary["collisions"]]
    # The contested file holds the kept page
    root = os.path.dirname(os.path.dirname(config.markdown_dir))
    text = open(os.path.join(root, collided[base + "/story.html"]["file"]), encoding="utf-8").read()
    assert f"url: {base}/story\n" in text or f"{base}/story\"" in text


def set_site(order, story="Story", story_html="Story HTML", with_story=True):
    Handler.pages = {"/": home(order), "/story.html": article(story_html)}
    if with_story:
        Handler.pages["/story"] = article(story)


class TestDeterministicCollisions:
    def test_reversed_order_on_rerun(self, server, tmp_path):
        set_site(["/story", "/story.html"])
        config, s1 = run(server, tmp_path)
        check_story_run(server, config, s1)
        set_site(["/story.html", "/story"])
        config, s2 = run(server, tmp_path)
        check_story_run(server, config, s2)
        assert server + "/story" in s2["unchanged_urls"]

    def test_loser_first_in_first_run(self, server, tmp_path):
        set_site(["/story.html", "/story"])
        config, s1 = run(server, tmp_path)
        check_story_run(server, config, s1)

    @pytest.mark.parametrize("concurrent", [1, 4])
    @pytest.mark.parametrize("incremental", [True, False])
    def test_concurrency_and_cache_state(self, server, tmp_path, concurrent, incremental):
        for order in (["/story", "/story.html"], ["/story.html", "/story"],
                      ["/story", "/story.html"]):
            set_site(order)
            config, summary = run(server, tmp_path, concurrent=concurrent, incremental=incremental)
            check_story_run(server, config, summary)

    def test_winner_content_changes(self, server, tmp_path):
        set_site(["/story", "/story.html"])
        run(server, tmp_path)
        set_site(["/story.html", "/story"], story="Story rewritten")
        config, summary = run(server, tmp_path)
        check_story_run(server, config, summary, winner_text="Story rewritten")
        assert summary["counts"]["changed"] >= 1

    def test_winner_removed_then_back(self, server, tmp_path):
        set_site(["/story", "/story.html"])
        run(server, tmp_path)
        set_site(["/story.html", "/story"], with_story=False)
        config, summary = run(server, tmp_path)
        files = md_by_url(config)
        # The winner has gone: the other page takes the file
        assert server + "/story.html" in files
        assert server + "/story" not in files
        assert summary["collisions"] == []
        set_site(["/story.html", "/story"])
        config, summary = run(server, tmp_path)
        check_story_run(server, config, summary)

    def test_default_mode(self, server, tmp_path):
        for order in (["/story.html", "/story"], ["/story", "/story.html"]):
            set_site(order)
            config, summary = run(server, tmp_path, markdown_only=False)
            check_story_run(server, config, summary)
            mirror = open(os.path.join(config.output_dir, "story.html"), encoding="utf-8").read()
            assert "Story HTML" not in mirror


PAIRS = [
    ("/about", "/about.html"),
    ("/news/slug-123", "/news/123/slug"),
    ("/blog/post-2026-10-01", "/blog/2026/10/01/post"),
]


class TestOnlyRealCollisions:
    @pytest.mark.parametrize("markdown_only", [True, False], ids=["md-only", "default"])
    @pytest.mark.parametrize("pair", PAIRS, ids=["about", "news", "blog"])
    def test_pair(self, server, tmp_path, pair, markdown_only):
        a, b = pair
        Handler.pages = {"/": home([a, b]), a: article("Page A"), b: article("Page B")}
        config, summary = run(server, tmp_path, markdown_only=markdown_only)
        files = md_by_url(config)
        root = os.path.dirname(os.path.dirname(config.markdown_dir))
        titles = {server + a: "Page A", server + b: "Page B"}
        for c in summary["collisions"]:
            # The contested file exists and holds the page that kept it (in
            # default mode it can be the HTML copy, which has no url line)
            text = open(os.path.join(root, c["file"]), encoding="utf-8").read()
            assert titles[c["kept"]] in text and titles[c["url"]] not in text
        lost = {c["url"] for c in summary["collisions"]}
        for url in (server + a, server + b):
            assert (url in files) != (url in lost), url
        if markdown_only:
            # Only a shared Markdown file is a collision in markdown-only mode
            paths = [files[u][0] for u in files if u in (server + a, server + b)]
            assert len(paths) == len(set(paths))
            if not lost:
                assert len(paths) == 2
