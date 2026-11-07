"""URLs that map to one output file, crawled from a local test server
(127.0.0.1 only). A page never replaces another URL's file: a later URL gets
a hash-suffixed name, and every URL keeps its file in later runs."""

import asyncio
import json
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from scrapepy.config import ScrapeConfig
from scrapepy.modes import crawl
from scrapepy.output.naming import url_suffix
from scrapepy.output.writer import create_output_dirs

REPO_ROOT = Path(__file__).resolve().parent.parent
WORDS = "Plenty of readable words appear here so the page passes the length check. " * 6


def article(title, token):
    return (f"<html><head><title>{title}</title></head><body><article><h1>{title}</h1>"
            f"<p>{WORDS}</p><p>{token}</p></article></body></html>")


def home(links):
    anchors = "".join(f'<p><a href="{href}">{href}</a></p>' for href in links)
    return (f"<html><head><title>Home</title></head><body><main><h1>Home</h1>"
            f"<p>{WORDS}</p><p>HOMEBODY</p>{anchors}</main></body></html>")


class Handler(BaseHTTPRequestHandler):
    pages: dict = {}       # path -> html, or an int status
    delay: dict = {}       # path -> seconds

    def do_GET(self):
        path = self.path.split("?")[0]
        time.sleep(Handler.delay.get(path, 0))
        page = Handler.pages.get(path, 404)
        if isinstance(page, int):
            self.send_response(page)
            self.end_headers()
            return
        data = page.encode()
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
    Handler.delay = {}
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


def md_files(config):
    """relative path -> (url, text) for every Markdown file."""
    found = {}
    for d, _, files in os.walk(config.markdown_dir):
        for f in files:
            path = os.path.join(d, f)
            text = open(path, encoding="utf-8").read()
            front = yaml.safe_load(text.split("\n---\n", 1)[0].removeprefix("---\n"))
            found[os.path.relpath(path, config.markdown_dir)] = (front["url"], text)
    return found


class Site:
    """Pages of a test site: path -> (title, token) or a status code."""

    def __init__(self):
        self.pages = {}
        self.order = []

    def serve(self):
        Handler.pages = {"/": home(self.order)}
        for path, page in self.pages.items():
            Handler.pages[path] = page if isinstance(page, int) else article(*page)


def check(base, site, config, summary, previous=None):
    """The invariants that hold after every run. Returns file -> url."""
    root = os.path.dirname(os.path.dirname(config.markdown_dir))
    files = md_files(config)
    fetched = [base + "/"] + [base + p for p in site.order]
    tokens = {p: page[1] for p, page in site.pages.items() if not isinstance(page, int)}
    tokens["/"] = "HOMEBODY"

    # Every distinct body fetched is in exactly one file
    for token in set(tokens[p] for p in ["/"] + site.order if p in tokens):
        holders = [f for f, (_, text) in files.items() if token in text]
        assert len(holders) == 1, (token, holders)

    # Every fetched page is in exactly one list
    written = set(_urls_from_markdown(summary, root))
    groups = {
        "written": written,
        "unchanged": set(summary["unchanged_urls"]),
        "duplicate": {d["url"] for d in summary["duplicates"]},
        "skipped": {s["url"] for s in summary["skipped_non_html"]},
        "failed": {f["url"] for f in summary["failed"]},
    }
    for url in fetched:
        where = [g for g, urls in groups.items() if url in urls]
        assert len(where) == 1, (url, where)

    # Unchanged and written pages have their file; duplicates point at a
    # file holding the same body
    by_url = {u: f for f, (u, _) in files.items()}
    for url in groups["unchanged"] | groups["written"]:
        assert url in by_url, url
    for d in summary["duplicates"]:
        same = open(os.path.join(root, d["same_as"]), encoding="utf-8").read()
        path = d["url"][len(base):]
        assert tokens[path] in same

    # Every suffixed page is in renamed, and renamed files exist
    renamed = {r["url"]: r for r in summary["renamed"]}
    for f, (url, _) in files.items():
        if f"-{url_suffix(url)}." in os.path.basename(f):
            met = url in groups["written"] | groups["unchanged"]
            assert not met or url in renamed, (url, f)
    for r in renamed.values():
        assert os.path.exists(os.path.join(root, r["file"]))
        assert r["file"] != r["natural_file"]

    # A file never changes URL between runs
    current = {f: u for f, (u, _) in files.items()}
    if previous is not None:
        for f, u in previous.items():
            if f in current:
                assert current[f] == u, (f, u, current[f])
    return current


def _urls_from_markdown(summary, root):
    """URLs of the pages written as new or changed in this run."""
    out = []
    for rel in summary["new_markdown"] + summary["changed_markdown"]:
        text = open(os.path.join(root, rel), encoding="utf-8").read()
        out.append(yaml.safe_load(text.split("\n---\n", 1)[0].removeprefix("---\n"))["url"])
    return out


VARIANTS = ["/{g}", "/{g}.html", "/{g}.htm", "/{g}/index.html"]


class TestNoPageIsLost:
    @pytest.mark.parametrize("order", [["/story.html", "/copy", "/story"],
                                       ["/story", "/copy", "/story.html"]])
    @pytest.mark.parametrize("incremental", [True, False])
    def test_body_shared_with_a_colliding_page(self, server, tmp_path, order, incremental):
        site = Site()
        site.pages = {"/story.html": ("Shared", "BODYA"), "/copy": ("Shared", "BODYA"),
                      "/story": ("Other", "BODYB")}
        site.order = order
        site.serve()
        previous = None
        for _ in range(3):
            config, summary = run(server, tmp_path, incremental=incremental)
            previous = check(server, site, config, summary,
                             previous if incremental else None)
        texts = [t for _, t in md_files(config).values()]
        assert sum("BODYA" in t for t in texts) == 1
        assert sum("BODYB" in t for t in texts) == 1

    def test_suffix_is_stable_and_listed(self, server, tmp_path):
        site = Site()
        site.pages = {"/story": ("One", "BODY1"), "/story.html": ("Two", "BODY2")}
        site.order = ["/story", "/story.html"]
        site.serve()
        config, s1 = run(server, tmp_path)
        files = {u: f for f, (u, _) in md_files(config).items()}
        assert files[server + "/story"] == "story/_index.md"
        assert files[server + "/story.html"] == f"story/_index-{url_suffix(server + '/story.html')}.md"
        assert [r["url"] for r in s1["renamed"]] == [server + "/story.html"]
        assert s1["counts"]["renamed"] == 1
        # Reverse the order: both keep their files, the suffixed one is listed
        site.order = ["/story.html", "/story"]
        site.serve()
        config, s2 = run(server, tmp_path)
        assert {u: f for f, (u, _) in md_files(config).items()} == files
        assert [r["url"] for r in s2["renamed"]] == [server + "/story.html"]

    def test_owner_not_met_yet_newcomer_suffixed(self, server, tmp_path):
        site = Site()
        site.pages = {"/story": ("One", "BODY1")}
        site.order = ["/story"]
        site.serve()
        run(server, tmp_path)
        site.pages["/story.html"] = ("Two", "BODY2")
        site.order = ["/story.html", "/story"]
        site.serve()
        config, summary = run(server, tmp_path)
        files = {u: f for f, (u, _) in md_files(config).items()}
        assert files[server + "/story"] == "story/_index.md"
        assert files[server + "/story.html"].startswith("story/_index-")

    def test_default_mode_pair_shares_the_suffix(self, server, tmp_path):
        site = Site()
        site.pages = {"/story": ("One", "BODY1"), "/story.html": ("Two", "BODY2")}
        site.order = ["/story", "/story.html"]
        site.serve()
        config, summary = run(server, tmp_path, markdown_only=False)
        check(server, site, config, summary)
        suffix = url_suffix(server + "/story.html")
        assert os.path.exists(os.path.join(config.output_dir, f"story-{suffix}.html"))
        files = {u: f for f, (u, _) in md_files(config).items()}
        assert files[server + "/story.html"].endswith(f"-{suffix}.md")
        mirror = open(os.path.join(config.output_dir, "story.html"), encoding="utf-8").read()
        assert "BODY1" in mirror


PAIRS = [
    ("/about", "/about.html"),
    ("/news/slug-123", "/news/123/slug"),
    ("/blog/post-2026-10-01", "/blog/2026/10/01/post"),
]


class TestPairsAreAllWritten:
    @pytest.mark.parametrize("markdown_only", [True, False], ids=["md-only", "default"])
    @pytest.mark.parametrize("pair", PAIRS, ids=["about", "news", "blog"])
    def test_pair(self, server, tmp_path, pair, markdown_only):
        site = Site()
        site.pages = {pair[0]: ("Page A", "BODYA"), pair[1]: ("Page B", "BODYB")}
        site.order = list(pair)
        site.serve()
        config, summary = run(server, tmp_path, markdown_only=markdown_only)
        check(server, site, config, summary)
        urls = {u for u, _ in md_files(config).values()}
        assert {server + pair[0], server + pair[1]} <= urls


def _random_site(rng):
    site = Site()
    counter = iter(range(10_000))
    for g in range(rng.randint(1, 3)):
        name = f"g{g}"
        variants = rng.sample(VARIANTS, rng.randint(2, 4))
        bodies = []
        for v in variants:
            path = v.format(g=name)
            if bodies and rng.random() < 0.3:
                site.pages[path] = rng.choice(bodies)       # identical page
            else:
                site.pages[path] = (f"Page {path}", f"BODY{next(counter)}X")
            bodies.append(site.pages[path])
        if rng.random() < 0.5:
            site.pages[f"/copy{g}"] = rng.choice(bodies)   # same body, own name
    site.order = list(site.pages)
    rng.shuffle(site.order)
    return site, counter


@pytest.mark.parametrize("seed", range(int(os.environ.get("SCRAPEPY_SEEDS", "8"))))
def test_random_sites_keep_every_page(server, tmp_path, seed):
    rng = random.Random(seed)
    site, counter = _random_site(rng)
    markdown_only = seed % 3 != 2
    previous = None

    def crawl_once(reset_previous=False):
        nonlocal previous
        rng.shuffle(site.order)
        site.serve()
        config, summary = run(server, tmp_path, markdown_only=markdown_only,
                              concurrent=rng.choice([1, 4]))
        previous = check(server, site, config, summary, None if reset_previous else previous)
        return config

    crawl_once()
    # A page's content changes, twice
    for _ in range(2):
        path = rng.choice([p for p in site.pages if not isinstance(site.pages[p], int)])
        site.pages[path] = (f"Page {path} again", f"BODY{next(counter)}X")
        crawl_once()
    # One page fails, another is removed, then both return
    live = [p for p in site.pages if not isinstance(site.pages[p], int)]
    failing, removed = rng.sample(live, 2)
    saved = dict(site.pages)
    site.pages[failing] = 500
    site.pages[removed] = 404
    crawl_once()
    site.pages = saved
    crawl_once()
    # The cache is deleted: files may change owner once, then hold again
    config = crawl_once()
    os.remove(os.path.join(config.output_dir, ".scrapepy_cache.json"))
    crawl_once(reset_previous=True)
    crawl_once()


class TestOldCaches:
    def _site(self):
        site = Site()
        site.pages = {"/story": ("One", "BODY1"), "/story.html": ("Two", "BODY2"),
                      "/copy": ("One", "BODY1")}
        site.order = ["/story", "/story.html", "/copy"]
        return site

    @pytest.mark.parametrize("kind", ["master", "round3"])
    def test_cache_without_assignments_converts_everything_once(self, server, tmp_path, kind):
        site = self._site()
        site.serve()
        config, _ = run(server, tmp_path)
        cache_path = os.path.join(config.output_dir, ".scrapepy_cache.json")
        data = json.load(open(cache_path, encoding="utf-8"))
        data.pop("assignments")
        if kind == "round3":
            data["files"] = {"../../MarkdownSites/x/story/_index.md": server + "/story.html"}
        json.dump(data, open(cache_path, "w", encoding="utf-8"))
        site.order.reverse()
        site.serve()
        config, summary = run(server, tmp_path)
        assert summary["unchanged_urls"] == []
        check(server, site, config, summary)
        config, summary = run(server, tmp_path)
        check(server, site, config, summary)
        assert set(summary["unchanged_urls"]) >= {server + "/story", server + "/story.html"}


def test_interrupted_run_then_normal_run(server, tmp_path):
    site = Site()
    site.pages = {f"/p{i}": (f"Page {i}", f"BODY{i}X") for i in range(6)}
    site.pages["/p0.html"] = ("Page 0 html", "BODYHX")
    site.order = list(site.pages)
    site.serve()
    Handler.delay = {p: 0.4 for p in site.pages}
    out = tmp_path / "out"
    out.mkdir()
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONPATH=str(REPO_ROOT))
    proc = subprocess.Popen(
        [sys.executable, "-m", "scrapepy", server + "/", "-o", str(out), "--wait", "0",
         "-c", "1", "--markdown-only"],
        cwd=str(out), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    time.sleep(2.0)
    proc.send_signal(signal.SIGINT)
    proc.communicate(timeout=60)
    Handler.delay = {}
    config, summary = run(server, out)
    check(server, site, config, summary)
    assert summary["status"] == "complete"
