"""Single page mode (-1) on a fake site served from 127.0.0.1.

The browser is replaced by a fetch from the local server that follows
redirects and reports the final address, as the browser does, so these
tests need no browser. The test at the end starts a real browser and runs
only with SCRAPEPY_BROWSER_TESTS=1. Every page here is invented.
"""

import asyncio
import json
import os
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from scrapepy.cli import parse_args
from scrapepy.modes import crawl as crawl_mode
from scrapepy.modes import single_page
from scrapepy.output.naming import url_suffix
from scrapepy.scraper import run_scraper

WORDS = " ".join(["Plain readable words make this page long enough to convert."] * 8)


def page(title, extra=""):
    return (f"<html><head><title>{title}</title></head><body><article><h1>{title}</h1>"
            f"<p>{WORDS}</p><p>{extra}</p></article></body></html>")


SITE = {
    "/": page("Home page"),
    "/guides/grow-tomatoes-on-a-balcony": page("Grow tomatoes on a balcony"),
    "/guides/keep-basil-alive-indoors": page(
        "Keep basil alive indoors",
        '<a href="/guides/grow-tomatoes-on-a-balcony">Tomatoes</a> <a href="/">Home</a>'),
    "/moved/new-home-for-the-story": page("New home for the story"),
    # Served only with the trailing slash: /docs answers 404
    "/docs/": page("Documentation home"),
    # Served at both addresses
    "/both": page("Both addresses, no slash"),
    "/both/": page("Both addresses, with slash"),
}
REDIRECTS = {"/old/story": "/moved/new-home-for-the-story"}


class Handler(BaseHTTPRequestHandler):
    log: list = []
    edits: dict = {}
    targets: list = []      # the addresses handed to the browser

    def do_GET(self):
        Handler.log.append(self.path)
        path, _, query = self.path.partition("?")
        if path in REDIRECTS or path == "/elsewhere":
            # /elsewhere moves to another host name for the same server
            port = self.server.server_address[1]
            target = (f"http://localhost:{port}/guides/keep-basil-alive-indoors"
                      if path == "/elsewhere" else REDIRECTS[path])
            self.send_response(301)
            self.send_header("Location", target)
            self.end_headers()
            return
        if path == "/search" and query:
            body = page("Search results", f"Results for {query}")
        else:
            body = SITE.get(path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        body = body.replace("</article>", Handler.edits.get(path, "") + "</article>")
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class FakeResponse:
    def __init__(self, url, status, html):
        self.url = url
        self.status = status
        self.body = html.encode("utf-8")
        self.encoding = "utf-8"


@pytest.fixture
def site(monkeypatch):
    Handler.log = []
    Handler.edits = {}
    Handler.targets = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    actions = []

    async def fake_browser(url, config, page_action=None, wait_time=None, **kwargs):
        actions.append(page_action)
        Handler.targets.append(url)
        try:
            address, hash_mark, fragment = url.partition("#")
            with urllib.request.urlopen(address, timeout=10) as r:
                # A browser keeps the fragment in the address it ends on
                return FakeResponse(r.geturl() + hash_mark + fragment, r.status,
                                    r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return FakeResponse(url, e.code, "")

    for module in (crawl_mode, single_page):
        monkeypatch.setattr(module, "fetch_page_browser", fake_browser, raising=False)
    yield f"http://127.0.0.1:{httpd.server_address[1]}", actions
    httpd.shutdown()
    httpd.server_close()


def run(url, out, *extra):
    config = parse_args(["-1", "--headless", url, "-o", str(out), "--wait", "0", *extra])
    code = asyncio.run(run_scraper(config))
    return config, code


def summary_of(config):
    return json.loads((Path(config.output_dir) / "run_summary.json").read_text())


def front(path):
    text = Path(path).read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---")[1])


def markdown_urls(config):
    """url -> Markdown file (relative to the site's Markdown folder)."""
    found = {}
    for md in Path(config.markdown_dir).rglob("*.md"):
        found[front(md)["url"]] = md.relative_to(config.markdown_dir).as_posix()
    return found


def test_two_pages_on_one_host_get_their_own_files(site, tmp_path):
    base, _ = site
    a = base + "/guides/grow-tomatoes-on-a-balcony"
    b = base + "/guides/keep-basil-alive-indoors"
    config, code = run(a, tmp_path)
    assert code == 0
    site_md = Path(config.markdown_dir).relative_to(tmp_path).as_posix()
    assert summary_of(config)["new_markdown"] == [
        f"{site_md}/guides/grow-tomatoes-on-a-balcony.md"]
    config, code = run(b, tmp_path)
    assert code == 0
    assert markdown_urls(config) == {
        a: "guides/grow-tomatoes-on-a-balcony.md",
        b: "guides/keep-basil-alive-indoors.md",
    }
    summary = summary_of(config)
    assert summary["counts"]["new"] == 1 and summary["counts"]["fetched"] == 1
    # The HTML copy is named after the page too
    assert (Path(config.output_dir) / "guides/keep-basil-alive-indoors.html").is_file()


def test_same_page_twice_is_unchanged(site, tmp_path):
    base, _ = site
    url = base + "/guides/grow-tomatoes-on-a-balcony"
    config, _ = run(url, tmp_path)
    md = Path(config.markdown_dir) / "guides/grow-tomatoes-on-a-balcony.md"
    first = md.read_text(encoding="utf-8")
    config, code = run(url, tmp_path)
    assert code == 0
    summary = summary_of(config)
    assert summary["unchanged_urls"] == [url]
    assert summary["counts"]["new"] == 0 and summary["counts"]["changed"] == 0
    assert md.read_text(encoding="utf-8") == first
    # A changed page updates its own file
    Handler.edits["/guides/grow-tomatoes-on-a-balcony"] = "<p>A new paragraph.</p>"
    config, code = run(url, tmp_path)
    summary = summary_of(config)
    assert summary["counts"]["changed"] == 1
    assert summary["changed_markdown"][0].endswith("guides/grow-tomatoes-on-a-balcony.md")
    assert "A new paragraph" in md.read_text(encoding="utf-8")
    assert len(list(Path(config.markdown_dir).rglob("*.md"))) == 1


def test_redirect_records_the_final_url(site, tmp_path):
    base, _ = site
    config, code = run(base + "/old/story", tmp_path)
    assert code == 0
    final = base + "/moved/new-home-for-the-story"
    assert markdown_urls(config) == {final: "moved/new-home-for-the-story.md"}
    assert front(Path(config.markdown_dir) / "moved/new-home-for-the-story.md")["url"] == final


def test_redirect_to_another_host_keeps_the_requested_url(site, tmp_path):
    base, _ = site
    config, code = run(base + "/elsewhere", tmp_path)
    assert code == 0
    # The page found its links, so it is classified as a section (_index.md)
    assert markdown_urls(config) == {base + "/elsewhere": "elsewhere/_index.md"}


def test_pages_with_a_query_string_keep_it_and_get_their_own_files(site, tmp_path):
    base, _ = site
    apples, pears = base + "/search?q=apples", base + "/search?q=pears"
    run(apples, tmp_path)
    config, _ = run(pears, tmp_path)
    files = markdown_urls(config)
    assert files[apples] == "search.md"
    assert files[pears] == f"search-{url_suffix(pears)}.md"
    assert summary_of(config)["renamed"][0]["url"] == pears
    # Each address is its own page for the incremental cache
    config, _ = run(apples, tmp_path)
    assert summary_of(config)["unchanged_urls"] == [apples]
    assert "?q=apples" in Handler.log[-1]


def test_old_index_file_is_left_alone(site, tmp_path):
    """Older versions wrote every -1 page to index/_index.md with the site's
    root as its url. A new run never writes another page into that file."""
    base, _ = site
    config = parse_args(["-1", base + "/", "-o", str(tmp_path)])
    old = Path(config.markdown_dir) / "index/_index.md"
    old.parent.mkdir(parents=True)
    old.write_text(f"---\ntitle: Old\nurl: {base}/\n---\n\nOld text\n", encoding="utf-8")
    config, code = run(base + "/guides/keep-basil-alive-indoors", tmp_path)
    assert code == 0
    assert old.read_text(encoding="utf-8").endswith("Old text\n")
    assert markdown_urls(config)[base + "/guides/keep-basil-alive-indoors"] == \
        "guides/keep-basil-alive-indoors.md"


def test_site_name_works_with_single_page(site, tmp_path):
    base, _ = site
    config, code = run(base + "/guides/keep-basil-alive-indoors", tmp_path,
                       "--site-name", "garden")
    assert code == 0
    assert config.output_dir.endswith(os.path.join("ScrapedSites", "garden"))
    assert (tmp_path / "MarkdownSites/garden/guides/keep-basil-alive-indoors.md").is_file()


@pytest.mark.parametrize("headless", [True, False])
@pytest.mark.parametrize("given,cache_key,file", [
    ("/docs/", "/docs/", "docs.md"),
    ("/docs/?page=2", "/docs/?page=2", "docs.md"),
    ("", "/", "index.md"),
    ("/#/route", "/#/route", "index.md"),
])
def test_page_is_fetched_exactly_as_given(site, tmp_path, headless, given, cache_key, file):
    """The browser gets the address as typed (trailing slash, query, empty
    path, fragment route); only the file name and cache key use the
    normalised path."""
    base, _ = site
    url = base + given
    args = ["-1", url, "-o", str(tmp_path), "--wait", "0"] + (["--headless"] if headless else [])
    config = parse_args(args)
    code = asyncio.run(run_scraper(config))
    assert code == 0
    assert Handler.targets == [url]
    expected_url = url if given else base + "/"
    found = markdown_urls(config)
    assert list(found) == [expected_url]
    # A page that found no links is an article; the home page is a section
    assert found[expected_url] in (file, file.replace(".md", "/_index.md"), "_index.md")
    cache = json.loads((Path(config.output_dir) / ".scrapepy_cache.json").read_text())
    assert list(cache["pages"]) == [cache_key]


def test_trailing_slash_is_its_own_cache_entry(site, tmp_path):
    """/both/ and /both are two addresses: each keeps its own cache entry, so
    running them in turn reports each one unchanged the second time."""
    base, _ = site
    for _ in range(2):
        run(base + "/both/", tmp_path)
        config, code = run(base + "/both", tmp_path)
        assert code == 0
    assert summary_of(config)["unchanged_urls"] == [base + "/both"]
    config, _ = run(base + "/both/", tmp_path)
    assert summary_of(config)["unchanged_urls"] == [base + "/both/"]
    cache = json.loads((Path(config.output_dir) / ".scrapepy_cache.json").read_text())
    assert sorted(cache["pages"]) == ["/both", "/both/"]
    assert len(markdown_urls(config)) == 2


def test_browser_wait_is_the_full_wait_only_with_a_page_action(monkeypatch):
    """-1 (with its page action) waits --wait; -j waits one second."""
    from scrapepy.config import ScrapeConfig
    seen = []

    async def fake(url, config, page_action=None, wait_time=None, **kwargs):
        seen.append((url, wait_time))
        return FakeResponse(url, 200, "<html></html>")

    monkeypatch.setattr(crawl_mode, "fetch_page_browser", fake)
    config = ScrapeConfig(url="https://example.com/app#/route", single_page=True)

    async def action(page):
        return page

    asyncio.run(crawl_mode._fetch_with_browser(config.url, config, action))
    asyncio.run(crawl_mode._fetch_with_browser(config.url, config))
    assert seen == [("https://example.com/app#/route", None),
                    ("https://example.com/app#/route", 1.0)]


def test_date_filter_does_not_skip_the_page(site, tmp_path):
    base, _ = site
    config, code = run(base + "/guides/keep-basil-alive-indoors", tmp_path, "--since", "2099-01-01")
    assert code == 0
    assert (Path(config.markdown_dir) / "guides/keep-basil-alive-indoors.md").is_file()
    # Only the page: no sitemap, feed or home page requests
    assert [p for p in Handler.log if p != "/robots.txt"] == ["/guides/keep-basil-alive-indoors"]


def test_browser_gets_the_scroll_action_and_no_link_is_followed(site, tmp_path):
    base, actions = site
    run(base + "/guides/keep-basil-alive-indoors", tmp_path)
    assert len(actions) == 1 and actions[0] is not None
    assert [p for p in Handler.log if p != "/robots.txt"] == ["/guides/keep-basil-alive-indoors"]


def test_missing_page_exits_1(site, tmp_path):
    base, _ = site
    config, code = run(base + "/no/such/page", tmp_path)
    assert code == 1
    assert summary_of(config)["failed"][0]["reason"] == "HTTP 404"


@pytest.mark.parametrize("extra", [["--max-depth", "0"], ["--include", "x"],
                                   ["--urls-file", "u.txt"], ["--markdown-only"]])
def test_other_options_stay_refused(extra, capsys):
    with pytest.raises(SystemExit):
        parse_args(["-1", "https://example.com/a", *extra])


@pytest.mark.parametrize("flag", ["--swagger", "-d"])
def test_site_name_still_refused_in_other_browser_modes(flag):
    with pytest.raises(SystemExit):
        parse_args([flag, "https://example.com/a", "--site-name", "x"])


@pytest.mark.skipif(os.environ.get("SCRAPEPY_BROWSER_TESTS") != "1",
                    reason="starts a browser: set SCRAPEPY_BROWSER_TESTS=1 with the browser "
                           "installed (scrapling install) to run it")
def test_real_browser_names_pages_by_their_url(tmp_path):
    Handler.log = []
    Handler.edits = {}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        run(base + "/old/story", tmp_path)
        run(base + "/docs/", tmp_path)
        config, code = run(base + "/guides/keep-basil-alive-indoors", tmp_path)
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert code == 0
    # Each page keeps its own file, named from its path (an article's .md,
    # or _index.md in a folder for a page classified as a section)
    found = markdown_urls(config)
    stems = {"/moved/new-home-for-the-story": "moved/new-home-for-the-story",
             "/docs/": "docs", "/guides/keep-basil-alive-indoors": "guides/keep-basil-alive-indoors"}
    assert set(found) == {base + p for p in stems}
    for p, stem in stems.items():
        assert found[base + p] in (stem + ".md", stem + "/_index.md")

