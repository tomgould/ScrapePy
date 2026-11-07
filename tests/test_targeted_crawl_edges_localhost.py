"""Edge cases of targeted crawls, on a fake site served from 127.0.0.1.

The server answers If-None-Match with 304, so re-runs in the same folder
exercise the incremental cache. Every page here is invented.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scrapepy.cli import parse_args
from scrapepy.config import ScrapeConfig
from scrapepy.processing.targeting import MAX_MATCH_CHARS, Targeting, check_pattern
from scrapepy.scraper import run_scraper

WORDS = " ".join(["Plain readable words make this page long enough to convert."] * 8)
RECIPES = ["/r/banana-oat-pancake-fingers", "/r/sweet-potato-mini-muffins",
           "/r/pea-and-mint-fritter-bites", "/r/apple-cinnamon-porridge-cups"]


def page(title, links=(), head=""):
    anchors = "".join(f'<p><a href="{h}">{h}</a></p>' for h in links)
    return (f"<html><head><title>{title}</title>{head}</head><body><article><h1>{title}</h1>"
            f"<p>{WORDS}</p>{anchors}</article></body></html>")


def recipe(path, links=()):
    data = json.dumps({"@type": "Recipe", "name": path, "recipeIngredient": ["1 egg"],
                       "recipeInstructions": ["Whisk the egg."]})
    return page(path, links, head=f'<script type="application/ld+json">{data}</script>')


def dated(title, links=()):
    return page(title, links, head='<meta property="article:published_time" '
                                   'content="2026-11-01T10:00:00+00:00">')


SITE = {
    # A listing with a rel="next" (written in capitals) and a /page/N link
    "/list": page("List", ["/list/page/2/", RECIPES[0]], head='<link rel="NEXT" href="/list-two">'),
    "/list-two": page("List two", [RECIPES[1]]),
    "/list/page/2": page("List page 2", [RECIPES[2]]),
    RECIPES[0]: recipe(RECIPES[0], ["/blog/page/2/"]),
    RECIPES[1]: recipe(RECIPES[1]),
    RECIPES[2]: recipe(RECIPES[2]),
    RECIPES[3]: recipe(RECIPES[3]),
    "/blog/page/2": page("Blog 2", ["/blog/page/3/"]),
    "/blog/page/3": page("Blog 3"),
    # -s scope: a dinner listing that also links the lunch listing's page 2
    "/category/dinner": page("Dinner", ["/category/dinner/page/2/", "/category/lunch/page/2/"]),
    "/category/dinner/page/2": page("Dinner 2"),
    "/category/lunch/page/2": page("Lunch 2"),
    # Depth: X is found two steps from the start before page 2 shows it one step away
    "/d": page("D", ["/d/aaa-first-article-here", "/d/page/2/"]),
    "/d/aaa-first-article-here": page("A", ["/d/xxx-shared-article-here"]),
    "/d/page/2": page("D2", ["/d/xxx-shared-article-here"]),
    "/d/xxx-shared-article-here": page("X"),
    # Date filter, for the prefetch test
    "/news": page("News", [f"/news/story-number-{i}-about-baking" for i in range(5)]),
    **{f"/news/story-number-{i}-about-baking": dated(f"Story {i}") for i in range(5)},
    # --site-name with -s
    "/docs": page("Docs", ["/docs/how-to-bake-sourdough-bread"]),
    "/docs/how-to-bake-sourdough-bread": page("Sourdough"),
}
SEARCH = page("Search", [RECIPES[3]])


class Handler(BaseHTTPRequestHandler):
    log: list = []

    def do_GET(self):
        Handler.log.append(self.path)
        path, _, query = self.path.partition("?")
        body = SEARCH if path == "/" and query else SITE.get(path.rstrip("/") or "/")
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        etag = f'"{abs(hash(body))}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def site():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


RECON = {"/robots.txt", "/sitemap.xml", "/", "/feed", "/feed/", "/rss", "/rss.xml",
         "/feed.xml", "/atom.xml", "/index.xml"}


def crawl(base, out, start, **kw):
    Handler.log = []
    kw.setdefault("wait", 0)
    kw.setdefault("concurrent", 1)
    kw.setdefault("markdown_only", True)
    config = ScrapeConfig(url=base + start, output_root=str(out), **kw)
    code = asyncio.run(run_scraper(config))
    summary = json.loads((Path(config.output_dir) / "run_summary.json").read_text())
    pages = [p for p in Handler.log if p not in RECON]
    return config, code, summary, pages


# 1. A re-run answered 304 follows the same pagination

@pytest.mark.parametrize("extra", [{}, {"max_depth": 0}], ids=["no-depth", "max-depth-0"])
def test_rerun_with_304_follows_the_same_pages(site, tmp_path, extra):
    kw = dict(follow_next=True, include=["^/nomatch"], **extra)
    _, _, _, first = crawl(site, tmp_path, "/list", **kw)
    assert sorted(first) == ["/list", "/list-two", "/list/page/2"]
    _, _, summary, second = crawl(site, tmp_path, "/list", **kw)
    assert sorted(second) == sorted(first)
    assert summary["counts"]["unchanged"] == 3


# 2. Pagination stays in scope

def test_footer_pagination_of_another_listing_is_not_followed(site, tmp_path):
    _, _, summary, pages = crawl(site, tmp_path, "/list", include=["^/r/"],
                                 follow_next=True, max_depth=1)
    assert "/blog/page/2" not in pages and "/blog/page/3" not in pages
    assert sorted(pages) == sorted(["/list", "/list-two", "/list/page/2"] + RECIPES[:3])


def test_pagination_outside_stay_in_path_is_not_followed(site, tmp_path):
    _, _, _, pages = crawl(site, tmp_path, "/category/dinner/", stay_in_path=True,
                           follow_next=True, max_depth=0, include=["^/nomatch"])
    assert pages == ["/category/dinner", "/category/dinner/page/2"]


# 3. Patterns

@pytest.mark.parametrize("pattern", ["(a+)+$", r"(\w*)*x", "(ab*)+"])
def test_nested_quantifiers_are_refused(pattern, capsys):
    assert check_pattern(pattern)
    with pytest.raises(SystemExit) as exc:
        parse_args(["https://site.test/", "--exclude", pattern])
    assert exc.value.code == 2
    assert "exponential" in capsys.readouterr().err


def test_patterns_see_at_most_the_first_2048_characters():
    config = ScrapeConfig(url="https://site.test/", exclude=["X$"], include=["^/"])
    t = Targeting(config, lambda link, path: link)
    assert t.allow("/" + "a" * (MAX_MATCH_CHARS + 10) + "X") is None
    assert t.allow("/aX") == "exclude"


# 4. Unbounded pagination notice

def test_follow_next_without_limits_prints_a_notice(site, tmp_path, capsys):
    crawl(site, tmp_path, "/list", follow_next=True, include=["^/nomatch"])
    assert "--follow-next without --max-pages or --max-depth" in capsys.readouterr().out
    crawl(site, tmp_path / "b", "/list", follow_next=True, include=["^/nomatch"], max_pages=5)
    assert "--follow-next without" not in capsys.readouterr().out


# 6. --urls-file

def test_urls_file_with_a_byte_order_mark(tmp_path):
    urls = tmp_path / "urls.txt"
    urls.write_bytes("﻿https://site.test/a\nhttps://site.test/b\n".encode("utf-8"))
    assert parse_args(["--urls-file", str(urls)]).start_urls == [
        "https://site.test/a", "https://site.test/b"]


def test_urls_file_errors_never_show_credentials(tmp_path, capsys):
    urls = tmp_path / "urls.txt"
    urls.write_text("https://alice:s3cretpw@site.test/a\nhttps://other.test/b\n")
    with pytest.raises(SystemExit):
        parse_args(["--urls-file", str(urls)])
    err = capsys.readouterr().err
    assert "line 2" in err and "s3cretpw" not in err and "alice" not in err


def test_urls_file_rejects_mixed_schemes(tmp_path, capsys):
    urls = tmp_path / "urls.txt"
    urls.write_text("http://site.test/a\nhttps://site.test/b\n")
    with pytest.raises(SystemExit) as exc:
        parse_args(["--urls-file", str(urls)])
    assert exc.value.code == 2
    assert "line 2: not on the same site and scheme" in capsys.readouterr().err


# 7. --max-depth 0 is recorded

def test_max_depth_zero_is_in_the_options(site, tmp_path):
    _, _, summary, _ = crawl(site, tmp_path, "/list", max_depth=0)
    assert summary["options"]["max_depth"] == 0


# 8. A start URL with a query is fetched as given with any option, or none

@pytest.mark.parametrize("extra", [
    {"exclude": ["^/blog"]}, {"max_pages": 3}, {"only_recipes": True},
    {"follow_next": True, "max_pages": 3}, {"site_name": "search"},
], ids=["exclude", "max-pages", "only-recipes", "follow-next", "site-name"])
def test_query_start_url_is_kept(site, tmp_path, extra):
    _, code, _, pages = crawl(site, tmp_path, "/?s=oats", **extra)
    assert code == 0
    assert pages[0] == "/?s=oats"
    assert RECIPES[3] in pages


def test_query_start_url_without_options_is_crawled_as_before(site, tmp_path):
    # No targeting option: the query is dropped and the crawl starts at the path
    # (this site has no home page, so the run ends with exit 1, as on master)
    crawl(site, tmp_path, "/?s=oats")
    assert not any("?" in p for p in Handler.log)
    assert Handler.log.count("/") >= 1


# 9. Tests that fail when the rules are weakened

def test_smallest_depth_wins(site, tmp_path):
    # X is first seen two steps away (from A), then one step away (page 2)
    _, _, summary, pages = crawl(site, tmp_path, "/d", max_depth=1, follow_next=True,
                                 include=["^/d/"])
    assert "/d/xxx-shared-article-here" in pages
    assert summary["targeting"]["skipped_by_depth"] == 0


def test_max_pages_with_a_date_filter_starts_no_prefetch(site, tmp_path):
    _, _, summary, pages = crawl(site, tmp_path, "/news", recent="4w", max_pages=2,
                                 concurrent=3)
    assert summary["counts"]["fetched"] == 2
    assert len(pages) == 2, pages        # no prefetched article beyond the cap


def test_site_name_keeps_the_starting_path_in_file_names(site, tmp_path):
    config, _, _, _ = crawl(site, tmp_path, "/docs/", stay_in_path=True, max_depth=1,
                            site_name="bakery")
    md = Path(config.markdown_dir)
    assert (md / "docs" / "how-to-bake-sourdough-bread.md").is_file()
    assert not (md / "how-to-bake-sourdough-bread.md").exists()
